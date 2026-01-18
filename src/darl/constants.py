from enum import Enum, auto

# TODO: should we have ERROR/ERROR_CAUGHT statuses? this requires getting info from cache as opposed to just
#       graph node_data. before we did get some info from cache (e.g. results) but those were only on demand,
#       not on every trace instantiation
class ExecutionStatus(Enum):
    COMPUTED = auto()
    FROM_CACHE = auto()
    ERRORED = auto()
    CAUGHT_ERROR = auto()
    NOT_RUN = auto()
  
