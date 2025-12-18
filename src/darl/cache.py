from dataclasses import dataclass
from typing import Any, List, Union


# TODO: should engine_id be added here? probably not since can get it from CacheEntryGraph looked up by graph_build_id
@dataclass
class CacheEntryResultMeta:
    # if everything was always perfectly deterministic wouldn't need graph_build_id or any checks derived from it
    graph_build_id: str
    # should duration_sec be recorded here? at very least needs to be recorded somewhere
    # else too, not tied to cached result
    duration_sec: float


@dataclass
class CacheEntryResult:
    result: Any


@dataclass
class CacheEntryGraph:
    graph: 'Graph'
    engine_id: str


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


# TODO: not stdlib so should not be in core?
class RedisCache(Cache):
    def __init__(self):
        import redis

        self.host = 'localhost'
        self.port = 6379
        self._r = redis.Redis(host=self.host, port=self.port)

    def __setstate__(self, state):
        import redis
        self.host = 'localhost'
        self.port = 6379
        self._r = redis.Redis(host=self.host, port=self.port)

    def __getstate__(self):
        return {}

    def get(self, key: str):
        import pickle
        return pickle.loads(self._r[key])

    def set(self, key: str, value):
        import pickle
        self._r.set(key, pickle.dumps(value))  # TODO: compress (lz4?)

    def contains(self, key: str):
        # TODO: implement this and bulk_contains properly, currently not efficient
        return key.encode() in self._r.keys()


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
