import pathlib
import pickle
import shutil
from dataclasses import dataclass
from typing import Any, List, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from darl.constants import ExecutionStatus
    from darl.graph import Graph


# TODO: should engine_id be added here? probably not since can get it from CacheEntryGraph looked up by graph_build_id
@dataclass
class CacheEntryResultMeta:
    # if everything was always perfectly deterministic wouldn't need graph_build_id or any checks derived from it
    graph_build_id: str


@dataclass
class CacheEntryResult:
    result: Any


@dataclass
class CacheEntryGraph:
    graph: 'Graph'
    engine_id: str


@dataclass
class CacheEntryGraphExecutionMeta:  # TODO: add memory info
    duration_sec: float
    status: 'ExecutionStatus'


class Cache:
    def get(self, key: str):
        raise NotImplementedError

    def bulk_get(self, keys: List[str]):
        # things like redis have a better bulk get implementation
        return [self.get(k) for k in keys]

    def set(self, key: str, value: Union['CacheEntryResult', 'CacheEntryResultMeta', 'CacheEntryGraph']):
        raise NotImplementedError

    def contains(self, key: str):
        raise NotImplementedError

    def bulk_contains(self, keys: List[str]):
        # things like redis have a better bulk contains implementation
        return [self.contains(k) for k in keys]

    def purge(self):
        raise NotImplementedError


class DictCache(Cache):
    def __init__(self):
        super().__init__()
        self._cache = {}

    # don't copy entire set local data cache on serialization
    # TODO: should we? I think no
    def __setstate__(self, state):
        self._cache = {}

    def __getstate__(self):
        return {}

    def get(self, key: str):
        return self._cache[key]

    def set(self, key: str, value: Union['CacheEntryResult', 'CacheEntryResultMeta', 'CacheEntryGraph']):
        self._cache[key] = value

    def contains(self, key: str):
        return key in self._cache

    def purge(self):
        self._cache.clear()


class DiskCache(Cache):
    def __init__(self, path: str, serializer=pickle.dumps, deserializer=pickle.loads):
        super().__init__()
        self._path = pathlib.Path(path)
        self._serializer = serializer
        self._deserializer = deserializer

        self._path.mkdir(parents=True, exist_ok=True)
        has_dirs = any(p.is_dir() for p in self._path.iterdir())
        if has_dirs:
            raise ValueError('cannot use a disk cache path that has subdirectories')

    def get(self, key: str):
        path = self._path / f'{key}.data'
        if not path.exists():
            raise KeyError(key)
        return self._deserializer(path.read_bytes())

    def set(self, key: str, value: Union['CacheEntryResult', 'CacheEntryResultMeta', 'CacheEntryGraph']):
        path = self._path / f'{key}.data'
        path.write_bytes(self._serializer(value))

    def contains(self, key: str):
        path = self._path / f'{key}.data'
        return path.exists()

    def purge(self):
        shutil.rmtree(self._path, ignore_errors=True)
        self._path.mkdir(parents=True, exist_ok=True)


# TODO: not stdlib so should not be in core?
class RedisCache(Cache):
    def __init__(self, namespace, host='localhost', port=6379,
                 serializer=pickle.dumps, deserializer=pickle.loads):
        import redis

        self.host = host
        self.port = port
        self._r = redis.Redis(host=self.host, port=self.port)
        self.namespace = namespace
        self._serializer = serializer
        self._deserializer = deserializer

    def __setstate__(self, state):
        import redis
        self.__dict__.update(state)
        self._r = redis.Redis(host=self.host, port=self.port)

    def __getstate__(self):
        return {
            'namespace': self.namespace,
            'host': self.host,
            'port': self.port,
            '_serializer': self._serializer,
            '_deserializer': self._deserializer,
        }

    def get(self, key: str):
        key = f'{self.namespace}-{key}'
        return self._deserializer(self._r[key])

    def set(self, key: str, value):
        key = f'{self.namespace}-{key}'
        self._r.set(key, self._serializer(value))  # TODO: compress (lz4?)

    def contains(self, key: str):
        # TODO: implement this and bulk_contains properly, currently not efficient
        key = f'{self.namespace}-{key}'
        return key.encode() in self._r.keys()

    def purge(self):
        pipe = self._r.pipeline()

        for key in self._r.scan_iter(match=f"{self.namespace}-*"):
            pipe.delete(key)

        pipe.execute()


class ThroughCache(Cache):  # should this be a subclass or protocol or something?
    def __init__(
            self,
            front_cache: Cache,
            back_cache: Cache,
            read_through=True,
            write_through=False,
            copy_on_read=False,
    ):
        self.front = front_cache
        self.back = back_cache
        self.read_through = read_through
        self.write_through = write_through
        self.copy_on_read = copy_on_read

    def get(self, key: str):
        try:
            return self.front.get(key)
        except KeyError:
            if self.read_through:
                ret_val = self.back.get(key)
                if self.copy_on_read:
                    self.front.set(key, ret_val)
                return ret_val
            else:
                raise

    def set(self, key: str, value: Union['CacheEntryResult', 'CacheEntryGraph']):
        self.front.set(key, value)
        if self.write_through:
            self.back.set(key, value)

    def contains(self, key: str):
        if self.front.contains(key):
            return True
        else:
            if self.read_through:
                return self.back.contains(key)
            else:
                return False

    def purge(self):
        raise NotImplementedError('purging behavior in a through cache is ambiguous, purge individual caches as desired')
