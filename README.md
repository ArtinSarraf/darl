# Darl

Darl is a lightweight code execution framework that transparently provides incremental computations, caching, scenario/shock analysis, parallel/distributed execution and more. The code you write closely resembles standard python code with some structural conventions added to automatically unlock these abilities. While the motivating usecase was computational modeling, the abilities provided by this library are broadly applicable across many different disciplines.

At a high level, Darl works by registering functions (or callable objects), which we call `Providers`, on a central object called an `Engine`. These providers are registered as a `Service` on the engine, with a name called the `Service Name`. The engine holds a mapping of `service name -> implementing provider`. Rather than invoking providers directly, they are invoked through the engine, which we refer to as a `Service Call` (service calls within a provider define what services the provider depends on, and for that reason services called within a provider are also referred to as dependencies). This allows us to (1) lazily build/compile a graph of computations for optimized execution, automatic caching/invalidation and more, and (2) update the `service -> provider` mapping to allow for alternative logic/scenarios to be executed without modifying source code. As a general rule, a service should be thought of, and named, as the "thing" you want (i.e. a noun), and the provider is a specific implementation of "how" you get the thing you want. 

Providers should be deterministic (and ideally pure) to ensure proper caching functionality (but a mechanism does exist to allow non-deterministic providers to integrate properly with the system. See the section on `Value Hashing` for more details on this). The cache key is determined by a provider's source code + input arguments + all depedent services' cache keys. If the provider is an instantiated class, the cache key also includes the source code of the parent classes and the instance attributes). If the source code of a provider changes (or the provider implementing a service changes) this would result in a new cache key for itself and all downstream nodes to compute in the computational graph. If a provider were to be non-determinstic then that means that injecting cached results from a previous run could provide incorrect results.

As suggested above, by the mention of "graph of computations" and "dependencies", under the hood darl is yet another DAG library. While this fact is mostly transparent to end users and they don't have to think in terms of graphs, it's still good to understand some of the basic concepts and implications of this. The main implication is that a single root execution needs to be defined within finite boundaries (i.e. no infinite loops or cycles). Also a computation graph is static, once it has been compiled. However, there are mechanisms for dynamically defining the graph during compile time. Each node in the graph is defined by a provider + the arguments passed to that provider (as well as some other metadata).

## Installation
`pip install darl`

Darl has no external dependencies. Standard library only.

## Usage
Full docs in progress

#### Quick Start
Below is a minimal working example to get started with darl. By convention service and provider names are pascal-cased like this: `MyService`, to distinguish them from standard functions in your project.

```
from darl import Engine

def Prediction(ngn, region):             # Providers take a reference to the engine (ngn) as their first argument
    model = ngn.FittedModel(region)      # This is a Service Call on the engine using the service name "FittedModel"
    data = ngn.Data()                         
    ngn.collect()                        # ngn.collect() is always called once after all Service Calls are completed   
    print('Running Prediction')          # print illustrates function executing to visualize caching capabilties
    return model + data           
                                                                                                   
def FittedModel(ngn, region):            # Service Names registered on the engine default to the name of the function
    data = ngn.Data()
    ngn.collect()                        # Want to put as little logic as possible to hit all service calls before ngn.collect()
    print('Running FittedModel')
    adj = {'East': 0, 'West': 1}[region]
    return data + 1 + adj
                                                                                                   
def Data(ngn):                                                                        
    print('Running Data')                # collect not needed if there's no service calls                                  
    return 1                                                                            

ngn = Engine.create([Prediction, FittedModel, Data])  # By default an in-memory dict cache is used, but cross-process persistent caches are available too

# Fresh run - all 3 providers are executed as shown by print statements
# `Data` only executed once, even though it's referenced twice due to darl's caching capabilities
print(ngn.Prediction('East'))  # -> 3

# Partially cached run - `Data` not executed, since the call to it in this run is identical to previous run
print(ngn.Prediction('West'))  # -> 4

# Fully cached run - no providers are executed
print(ngn.Prediction('West'))  # -> 4
```

#### Updates and Shocks
An engine internally holds a mapping of a service name to the provider that implements that service. This mapping can be updated to add services or transparently provide the desired results from another source, without having to update source code to reroute the calls. The `Engine` object provides an `update` method to achieve this.

```
# Continuing from previous section code snippet

def MoreData(ngn, region):                    # New service/provider to add
    data = ngn.Data()
    ngn.collect()
    print('Running MoreData')
    return data + {'East': 98, 'West': 99}[region]

def FittedRandomForestModel(ngn, region):     # New provider for existing service to update
    data = ngn.Data()
    more_data = ngn.MoreData(region)
    ngn.collect()
    print('Running FittedModel')
    return data + more_data

# By default updates return a new engine, leaving the original engine untouched
ngn2 = ngn.update({
    'FittedModel': FittedRandomForestModel,         # Now any ngn.FittedModel service calls within ngn2 providers will route to FittedRandomForestModel
    'MoreData': MoreData
})
print(ngn2.Prediction('West'))  # -> 102            # Note: `Data` still unchanged/cached from last run, not executed
print(ngn.Prediction('West'))   # -> 4              # Original ngn object still routes to original FittedModel
```

Updates require providing an explicit and full implementing provider. Another option for updating logic within an engine is a shock. A shock applies a provided function onto the output of a specified service. The `Engine` object provides a `shock` method to achieve this.

```
ngn3 = ngn2.shock('Data', lambda x: x + 1)  # Now any Data service calls within ngn3 providers will return the original result with the function applied
print(ngn3.Prediction('West'))  # -> 104
```

##### Scoped Updates and Shocks
Updates and shocks by default apply to all service calls within an engine. However, it is possible to isolate these modifications to a specific scope within a run. For example in the snippets provided so far, in ngn2 (the one with `FittedRandomForestModel`) `Data` is called in 3 different places or paths:
```
(1) Prediction -> Data
(2) Prediction -> FittedModel -> Data
(3) Prediction -> FittedModel -> MoreData -> Data
```
Let's say you only want to modify `Data` when called in paths (2, 3) i.e. in the scope of the `FittedModel` service, you can do what is called a scoped update, or scoped shock.

```
def Data_for_FittedModel():
    print('Running Data for FittedModel')                                               
    return 2   

ngn4 = ngn2.update({'Data': Data_for_FittedModel}, scope=('FittedModel',))
print(ngn4.Prediction('West'))  # -> 104

ngn5 = ngn2.shock('Data', lambda x: x + 1, scope=('FittedModel',))
print(ngn5.Prediction('West'))  # -> 104
```

You can specify an even narrower scope by defining a compound scope. For example, let's say you want to limit your modifications to path (3), i.e. in the scope of `FittedModel, MoreData`, you can define your scope with `scope=('FittedModel', 'MoreData')`. Note that this will apply the modification to any path where those services exist in that order, even if they are not directly preceeding either. So, this would still be applied in a path like this: `Prediction -> FittedModel -> XService -> MoreData -> Data`.

```
ngn6 = ngn2.update({'Data': Data_for_FittedModel}, scope=('FittedModel', 'MoreData'))
print(ngn6.Prediction('West'))  # -> 103
```

(See the "Pre-compilation" section below for a practical use case of scoped updates.) 

#### Error Handling
Errors within a Service Call cannot be handled with the regular try/except mechanism due to darl's lazy execution. Instead, errors can be propagated and handled as regular values, in the form of an error sentinel. To enable this, add the `ngn.catch` modifier to the ngn call, this will force any service call that encounters an error to return an error sentinel.

```
from darl import Engine

def A(ngn):                                                           
    b = ngn.catch.B()                     # any errors will be caught here in an error sentinel             
    ngn.collect()       
    
    match b:                              # error sentinel can be handled like a regular value
        case ngn.error(RuntimeError()):
            b = 999
        case ngn.error():                 # any other error type 
            raise b.error   
        case _:
            pass
    return b + 1           
                                                                                                   
def B(ngn):                                                                
    raise RuntimeError

ngn = Engine.create([A, B])
print(ngn.A())  # -> 1000
```

#### Pseudo Static Typing
Darl provides a mechanism for a pseudo form of static typing. The expected type of a service call result can be specified using the `ngn.type` modifier. To enforce type checking add `enforce_types=True` to the Engine instantiation. 
Currently, the static typing capabilities are extremely limited. Exact type matches only, no return type checking and minimal info about the type mismatches, this will be improved in future versions.

```
from darl import Engine

def A(ngn) -> int:
    b = ngn.type[int].B()  # matches return type of provider - pass
    b2 = ngn.B()           # no expected type always passes - pass
    c = ngn.type[int].C()  # mismatch with provider return type - fail
    d = ngn.type[int].D()  # has expected type, but provider has no return type - fail
    ngn.collect()
    return b + b2 + c + d

def B(ngn) -> int:
    return 1

def C(ngn) -> str:
    return 'a'

def D(ngn):
    return 9

ngn = Engine.create([A, B, C, D], enforce_types=True)
print(ngn.A())
```

#### Parallel Execution
Typically, coding parallel execution schemes requires explicit parallelization constructs and management. Darl makes parallel execution/coding completely transparent to users and look just like plain sequential execution. This can be achieved due to the fact that darl lazily builds a computation graph. This computation graph can then be passed to different types of executors including parallel graph executors.

```python
import time
from darl import Engine
from darl.cache import DiskCache
from darl.execution.dask import DaskRunner
from dask.distributed import Client                                                  # requires installing dask/dask.distributed

def NorthAmericaGDP(ngn):
    gdps = []
    for country in ['USA', 'Canada', 'Mexico']:                                      # for loop over service call is automatically parallelized
        gdp = ngn.NationalGDP(country)
        gdps.append(gdp)
    ngn.collect()
    return sum(gdps)

def NationalGDP(ngn, country):
    if country == 'USA':
        gdps = [ngn.RegionalGDP(country, region) for region in ('East', 'West')]     # comprehensions of service calls automatically parallelized too
        ngn.collect()
        return sum(gdps)
    else:
        ngn.collect()
        time.sleep(2)
        return {
            'Canada': 10,
            'Mexico': 10,
        }[country]

def RegionalGDP(ngn, country, region):
    time.sleep(2)
    return {
        ('USA', 'East'): 10,
        ('USA', 'West'): 10,
    }[(country, region)]

cache = DiskCache('/tmp/darl_parallel_example')  # Need to use a cross process persistent cache (i.e. not DictCache); use another temp disk cache path if necessary
cache.purge()                                    # So result doesn't pull from cache
client = Client()                                # Automatically sets up a local multiprocess dask cluster; Can set up dask cluster to be distributed as well, in which case cache needs to be accessible from all distributed worker instances (e.g. RedisCache)
runner = DaskRunner(client=client)
ngn = Engine.create([NorthAmericaGDP, NationalGDP, RegionalGDP], runner=runner, cache=cache)
ngn.NorthAmericaGDP()  # ~2 sec, instead of ~8 seconds for sequential run
```

The runner backend is configurable and customizable, users can even define their own by implementing the Runner interface found here: `darl.execution.base.Runner`. Currently only a local SequentialRunner and a DaskRunner are available. A RayRunner is also in the works.

Fun fact: The Dask and Ray distributed compute libraries provide functionality for tagging tasks and workers with resources to route specific tasks to specific workers. This functionality can be used to create distributed clusters with less memory overall than a naive cluster would require. Normally, you would need to allocate enough memory for each worker to be able to handle the memory load of the largest task. But with this functionality you can create a cluster of tiered memory workers, to better match the memory profile of your tasks. Depending on the distribution of your tasks' memory consumption you can allocate mostly low memory workers with few high memory workers, resulting in relatively cheap clusters on cloud providers.

#### Debugging: Tracing and Replaying
Darl provides the ability to programatically trace through your executed runs and even replay/rerun subsections of them. The trace provides an object that can navigate through an execution of a computational graph. Seeing results and metadata at all intermediate steps. Calling `ngn.trace()` will provide the trace for the previous executed run on the engine. You can pass a specific run id to ngn.trace as to see the trace for previous runs. These ids can be found using `ngn.executed_graph_build_ids`. You can even navigate through traces that were run in other processes, as long as you have the graph_build_id and can recreate the cache (e.g. you have a persistent cache like a DiskCache or a RedisCache, not just the default DictCache). Any exception that is raised from within a provider is wrapped in a `ProviderException` which provides the `graph_build_id` and `cache_key` for the failed node in the graph. Assuming you still have access to or a way to recreate the cache used for that run you can use that info to instantiate a trace for that run and debug remotely. Currently, the traceback for exceptions raised within user code can be drowned out by several layers of framework code, there are plans in place to revamp the darl tracebacks to be more succicnt and easily parseable. For now the trace capabilities should allow for easier navigation and identifcation of errors.

```
def A(ngn):
    b = ngn.B()
    ngn.collect()
    return b + 1

def B(ngn):
    b = ngn.catch.B2()
    ngn.collect()
    match b:
        case ngn.error():
            raise b.error
        case _:
            return b

def B2(ngn):
    c = ngn.C()
    d = ngn.D()
    ngn.collect()
    return c / d    # raise a ZeroDivisionError

def C(ngn):
    return 1

def D(ngn):
    return 0

ngn = Engine.create([A, B, B2, C, D])
ngn.D()  # precache D
try:
    ngn.A()         # This will and should fail for demonstration purposes
except:
    pass

trace = ngn.trace()
print(trace)                                         # -> <Trace: <CallKey(A: {}, ())>, NOT_RUN>
print(trace.upstreams[0])                            # -> <Trace: <CallKey(B: {}, ())>, ERRORED>, (0.0 sec)>
print(trace.upstreams[0].upstreams[0])               # -> <Trace: <CallKey(B2: {}, ())>, CAUGHT_ERROR>, (0.0 sec)>
print(trace.upstreams[0].upstreams[0].upstreams[0])  # -> <Trace: <CallKey(C: {}, ())>, COMPUTED>, (0.0 sec)>     # Order of this one and below might be swapped
print(trace.upstreams[0].upstreams[0].upstreams[1])  # -> <Trace: <CallKey(D: {}, ())>, FROM_CACHE>

# can use trace.ups as shorthand for trace.upstreams and trace.downs for trace.downstreams
print(trace.ups[0].ups[0].ups[0].result)       # -> 1    # Order of this one and below might be swapped
print(trace.ups[0].ups[0].ups[1].result)       # -> 0
```

To replay a specific node in the computational graph you can simply call `trace.replay()` on any intermediate trace. This will grab all the dependent service results from cache and provide them to the associated provider. Giving an exact recreation of the run as it originally happened, which is extremely useful for reproducible debugging. In the example above say we want to replay the failed node to step through it in the debugger:

```
# in ipython or jupyter notebook for the %debug magic
# put a breakpoint in your failed provider and continue to it
%debug trace.ups[0].replay()
```
This way you can now see all the intermediate results that led to a failed execution of your provider.

To retrieve a trace from another process you can do the following:
```
from darl.trace import Trace

graph_build_id = ...                 # retrieved from logs or terminal or something like that (provided in darl tracebacks)
cache = DiskCache('/tmp/some_path')  # same persistent cache that the graph_build_id run was executed with
trace = Trace.from_graph_build_id(graph_build_id, cache=cache)
```

#### Value Hashing
It was previously mentioned that providers should be deterministic. However, in a real world scenario you'll almost always need a provider that returns data from an external source, like a database, file or network request. These cannot be deterministic with respect to the providers cache key since they depend on some external state. Take the following code for example:

```
external_data = [1, 2, 3]

def Data(ngn):
    return external_data

def Result(ngn):
    data = ngn.Data()
    ngn.collect()
    return sum(data)

ngn = Engine.create([Result, Data])
print(ngn.Result())  # -> 6

external_data = [5, 6, 7]                             # Represents external data changing

ngn = Engine.create([Result, Data], cache=ngn.cache)  # Reinstantiate engine and persist cache from previous run
print(ngn.Result())  # -> 6                           # This is wrong and pulling a stale incorrect cache result
```

This provides an incorrect result because the `Data` cache key never changes, even though the underlying data does. Darl provides a way to integrate non-deterministic providers with proper caching through something called value hashing. A provider can be marked with the `@value_hashed` decorator to force the cache key to be dependent on the result of the provider. During the graph building stage the provider is eagerly executed and the hash of the provider result is used as the cache key. It looks like this (if you rerun the above snippet with this decorator below you would get the correct result on the second ngn.Result() call):

```
from darl import value_hashed

@value_hashed
def Data(ngn):
    return external_data
```

Since value hashed providers need to be eagerly executed it might not be ideal to have them execute during the lazy graph build if they take a significant amount of time. A common pattern to combat this issue is to value hash a provider that retrieves a smaller subset of data from the external source that the total set of data is determinstic with respect to. For example:

```
# This is not a working exmaple, just for illustrative purposes

@value_hashed
def DataToken(ngn):
    mark_date = ngn.mark_date()
    ngn.collect()
    return connection.query('select max(modified_time) from data')

def Data(ngn):
    token = ngn.DataToken()
    ngn.collect()
    return connection.query(f"select * from data where modified_time <= '{token}'")
```

This way the `DataToken` provider is quick to run and if the underlying data changes, it will give a new value which will result in a new cache key and invalidate any previously cached result for `Data`.

Note: Value hashed results are cached across service call invocations on a single engine. So even if the underlying value were to change, on a single engine you'd get the first result computed. This holds true even if you make an `update` on the engine. To force recompute of a value hashed result you can either (1) reinstantiate the engine, or (2) call `ngn = ngn.copy()`.

#### Edge Dependencies, Inlining and Iter

Here we'll start talking a bit more about some of the syntax restrictions of darl providers. It was previously mentioned that all service calls need to occur before `ngn.collect()`. To expand on this restriction, before the ngn.collect call, all results from a service call are not actual resolved values, but rather proxy objects with limitations on the operations that can be performed on them. After the ngn.collect() call these values are the actual resolved values and can be treated as such without any limitations. Some of these restrictions include:
1. no boolean operations or conditional logic
2. no iteration over the results
3. no instance type checking as this will resolve incorrectly to the proxy class

Most other types of operations can actually be safely performed on the proxy object (e.g. method calls, arithmetic, etc.). If these restricted operations are absolutely necessary there are mechanisms to work around them.

The first mechanism is called inlining. Inlining a service call means that the service call is fully executed eagerly and returns the resolved value instead of a proxy. This is similar to the idea of value hashing, however, the cache key is still dependent on the original cache key in the inlining case. Inlining a service call is achieved by adding the `ngn.inline` modifier to the service call. Inlining should be used sparingly and only as a last resort or in cases where the performance of eagerly executing a service is negligible. An example would look like this:

```
def A(ngn):
    b = ngn.inline.B()   # `b` is a fully resolved value due to inline
    if b > 1:            # without ngn.inline above this would fail here
        c = ngn.C()
    else:
        c = 0
    ngn.collect()
    return b + c

def B(ngn):
    c = ngn.C()          # `c` is a proxy object before ngn.collect() call
    ngn.collect()
    if c > 2:            # ngn.inline NOT needed here since if statement is after ngn.collect()   
        return 1
    else:
        return 2

ngn = Engine.create([A, B])
ngn.C = 3
print(ngn.A())  # -> 1
```

The next mechanism is the iter modifier. This allows you to iterate through a service call proxy result without needing to eagerly evaluate the service. The drawback of this mechanism is that an explicit number of iterations integer (`n`) needs to be provided, even if the exact `n` is not known. The concerns of this drawback are mitigated by the fact that `n` can safely be an overestimate and will explicitly fail if `n` is an underestimate. Here is an example of the ngn.iter modifier:

```
def A(ngn):
    ys = []
    m, b = ngn.iter(n=2).Coeffs()   # iter can be used for tuple unpacking too (or you can assign to one variable and unpack after collect)
    for x in ngn.iter(n=7).Xs():    # can see here we set n=7 even though actually n is 3, but still succeeds. if we set n <= 2 this would raise an error
        y = m * ngn.Y(x) + b        # # if not passing iterated results to another service, can likely just iterate after ngn.collect, and no iter needed
        ys.append(y)
    ngn.collect()
    return sum(ys)

def Xs(ngn):
    return [1, 2, 3]

def Y(ngn, x):
    return x + 1

def Coeffs(ngn):
    return (3, 2)

ngn = Engine.create(
    [A, Xs, Y, Coeffs],
    allow_edge_dependencies=True   # edge_dependencies will be discussed next
)
print(ngn.A())  # -> 33
```

The snippet above introduced a new term "edge dependency". Previously we knew of dependencies as the service calls that a provider makes. For example, in the very first quick start code snippet, the provider `Prediction` depends on the services `FittedModel` and `Data`. However, this is not the only way that a dependency can be introduced to a provider. Take the following simple example:

```
def A(ngn):
   b = ngn.B()
   c = ngn.C(b)
   ngn.collect()
   return b + c

...  # don't worry about B and D for the purpose of this explanation

def C(ngn, x):
    d = ngn.D()
    ngn.collect()
    return d + x
```

Here provider `C` directly depends on service `D`. However, within provider `A`, `C` is passed the result of `B` which means that `C` in this specific case also depends on `B`. These dependencies that are intoduced by passing service results to other services are called `edge dependencies`. Ideally edge dependencies should be factored out to direct dependcies where possible, and for that reason, by default edge dependencies are disabled. However, in cases where edge dependencies are desired they can be enabled by passing `Engine.create(..., allow_edge_dependencies=True)`. 

One limitation of edge dependencies is that a service result can only be passed to another service if it hasn't been operated on. For example the following is not allowed and will result in a failure. This is overly sensitive restriction and is because it is unknown if a method call will modify the object in place, which can cause issues with the caching mechanism.

```
def A(ngn):
    b = ngn.B()
    b = b.lower()  # can't apply a method to a service result and then pass it into another service
    c = ngn.C(b)
    ngn.collect()
    return c

def B(ngn):
    return 'ABC'

def C(ngn, b):
    return b[0]

ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
ngn.A()  # fails with `DepProxyException: Attempted to pass in a dep proxy which has an operation applied to it, this is not allowed`
```

To work around this you could introduce another intermediate service to just do the desired operation, like so:

```
def A(ngn):
    b = ngn.B()
    b = ngn.IntermediateLower(b)  # change from operation on service result to just another edge dependency
    c = ngn.C(b)
    ngn.collect()
    return c

def IntermediateLower(ngn, x):
    return x.lower()

def B(ngn):
    return 'ABC'

def C(ngn, b):
    return b[0]

ngn = Engine.create([A, B, C, IntermediateLower], allow_edge_dependencies=True)
print(ngn.A())  # -> 'a'
```

This works, but leaves much to be desired due to the verbosity of adding a new provider. This can be solved using anonymous services discussed in the next section.


#### Anonymous Services
Anonymous (aka anon) services allow us to call a provider through the engine without registering it. This means we can also call lambda functions as if they were services. They are called anonymous because they do not have a service name associated with them registered in the engine. An anon service is invoked by passing a callable to ngn like so: `ngn[some_callable_or lambda](...)`. Anon services also need to be explicitly enabled like so: `Engine.create(..., allow_anon_services=True)`.

Take the example from the snippet above, instead of inserting an intermediate service we can now do this instead:

```
def A(ngn):
    b = ngn.B()
    b = ngn[lambda ngn, x: x.lower()](b)  # anon service here
    c = ngn.C(b)
    ngn.collect()
    return c

def B(ngn):
    return 'ABC'

def C(ngn, b):
    return b[0]

ngn = Engine.create([A, B, C], allow_edge_dependencies=True, allow_anon_services=True)
print(ngn.A())  # -> 'a'
```

#### Embeddable
Embeddable functions are non-provider functions that take in a engine and make service calls that can be called within a provider. These functions follow the same rules as a provider the only difference being they are not invoked through the engine at all, just called directly. They need to be decorated with `@embeddable` so that the ngn.collect() machinery works properly. For example:

```
from darl import embeddable

@embeddable
def some_common_operation(ngn, data):
    x = ngn.X()
    ngn.collect()
    return data * x

def A(ngn):
    b = ngn.B()
    b = some_common_operation(ngn, b)
    c = ngn.C()
    ngn.collect()
    return b + c + 1

ngn = Engine.create([A])
ngn.X = 2
ngn.B = 1
ngn.C = 1

print(ngn.A())  # -> 4
```

Without the `@embeddable` provider A would have exited at the `ngn.collect()` in `some_common_operation` and the `ngn.C()` service call would not have been captured.

#### Pre-compilation and pre-caching
Recall that we call the stage of building the computation graph "compilation". Depending on the size, complexity and dynamism (i.e. amount of eager execution) of the graph being built this step can take more time than is desired for optimal execution. Darl provides a way to pre-compile the graph separately from execution, allowing you to skip this step and execute at a later time when the conditions to run are met. In cases where pre-compilation is being employed, it's important to make sure that the structure of the graph being compiled is not dependent on the conditions to run that are being waited on. This can happen if you have control flow logic that causes different service calls to be made based on eager evaluation during the compile step.

Consider the following example scenario where pre-compilation could be useful: You have a model that you run daily, but there's a prediction dataset that you need to wait on before you run. You want the model to run and finish asap once the dataset becomes available. By pre-compiling your model you can trigger the execution phase immediately once the data becomes available saving any compilation time you would have otherwise had to pay.

Now consider the fact that while we're waiting on the prediction dataset to become available, we could actually run a significant portion of our model that isn't dependent on that dataset. For example we could gather training datasets and pre-calibrate the model. Then once the prediciton data is avialable we could then incrementally compute only the parts that depend on prediction data. We've already touched on this before, but this is made possible by pre-caching our model.

An example of pre-caching and pre-compilation looks like this:

```
from darl.execution.sequential import SequentialRunner

def Prediction(ngn):
    model = ngn.FittedModel()
    data = ngn.PredictData()                         
    ngn.collect()
    print('Running Prediction')
    return model + data           
                                                                                                   
def FittedModel(ngn):
    data = ngn.TrainData()
    ngn.collect()
    print('Running FittedModel')
    return data + 1
                                                                                                   
def TrainData(ngn):
    md = ngn.MarkDate()
    ngn.collect()                                                                     
    print('Running TrainData')
    return {'yesterday': 3, 'today': 4}[md]
                                                                                                   
def PredictData(ngn):                                   # Imagine the data for "today" in this provider is not available until "later"
    md = ngn.MarkDate()
    ngn.collect()
    print('Running PredictData')
    return {'yesterday': 1, 'today': 2}[md]

class MarkDate:
    def __init__(self, date_offset=0):
        self.date_offset = date_offset

    def __call__(self, ngn):
        print('Running MarkDate')
        return {0: 'today', -1: 'yesterday'}[self.date_offset]

ngn = Engine.create([Prediction, FittedModel, TrainData, PredictData, MarkDate()])

# Pre-cache
ngn_precache = ngn.update(
    {'MarkDate': MarkDate(-1)},                         # Today's predict data not available yet, but yesterday's is
    scope=('PredictData',)                              # Only update date in scope of PredictData, TrainData will still use today's data so no re-calibration later
)
ngn_precache.Prediction()  # -> 6                       # All the calibration will remain cached from this run for incremental prediction data run

# Pre-compile
graph = ngn.compile(ngn.callkey.Prediction())           # This graph object can also be inspected for various purposes
runner = SequentialRunner()

# Execute (when predict data becomes available)
runner.run(graph, ngn.cache)  # -> 7                    # You'll notice only PredictData and Prediction are executed
```

(TODO: discuss JITing)

#### Factory Functions
A common pattern for instantiating engines for different models is to have what is called a factory function. There is nothing special about these functions besides that within them the necessary providers are gathered, and engine is instantiated and returned. However, breaking up the creation of an engine for a single model/process in this way can make projects much more manageable.

Here is a non-working pseudocode example:

```
# project/factory.py

def create_modelA_ngn(option1=False, option2=False):
    from project.modelA import SomeProvider, SomeOtherProvider
    from project.cache import SomePersistentCache

    providers = recursively_find_providers('project.modelA')  # Not a currently existing function within darl library

    ngn = Engine.create(providers, cache=SomePersistentCache())

    if option1:
        ngn = ngn.update({'SomeService': SomeProvider})
    if option2:
        ngn = ngn.update({'SomeService': SomeOtherProvider})
    return ngn


# project/script.py
from project.factory import create_modelA_ngn

ngn = create_modelA_ngn(option1=True)
ngn = ngn.RootService()
```


#### Cache Types

Darl allows for configurable and customizable caching backends. By default, an in-memory dict cache is used. This means that cached results will not live beyond the scope of the process in which they are cached. While this can be useful in many ways, it also limits the usefulness of caching in many other ways. For example cached results cannot be shared across different users or processes. This can be solved by using a persistent cache, for example a DiskCache or RedisCache, both of which can be used by multiple users and processes. Users can also define their own cache backends by implementing a simple interface found here: `darl.cache.Cache`. Alternative cache backends can be passed in like this:

```
from darl.cache import Cache

# Custom defined cache backend
class DiskCache(Cache):
    def __init__(self, path: str):
        super().__init__()
        self._path = pathlib.Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        has_dirs = any(p.is_dir() for p in self._path.iterdir())
        if has_dirs:
            raise ValueError('cannot use a disk cache path that has subdirectories')

    def get(self, key: str) -> 'Any':
        path = self._path / f'{key}.data'
        if not path.exists():
            raise KeyError(key)
        return pickle.loads(path.read_bytes())

    def set(self, key: str, value: 'Any'):
        path = self._path / f'{key}.data'
        path.write_bytes(pickle.dumps(value))

    def contains(self, key: str) -> bool:
        path = self._path / f'{key}.data'
        return path.exists()

    def purge(self):
        shutil.rmtree(self._path, ignore_errors=True)
        self._path.mkdir(parents=True, exist_ok=True)

ngn = Engine.create(..., cache=DiskCache('/tmp'))
```

Darl also provides something called a `ThroughCache` which allows you to compound two different caches together. The ThroughCache has a front and back cache and a few different configurations for falling back reads and pushing through writes to the back cache. For example:

```
from darl.cache import ThroughCache, DiskCache, DictCache

thru_cache = ThroughCache(
    front_cache=DictCache(),
    back_cache=DiskCache('/tmp'),
    read_through=True,             # If not in front cache, check in back cache?
    write_through=False,           # Write into front cache and then back cache?
    copy_on_read=False,            # If falling back to read back cache, copy the data into front cache?
)
```

This specific configuration can be useful for situations like running several scenarios. For the base run you want to cache everything so that scenarios only run incremental scenario sensitive parts. For the individual scenario runs you don't need to cache all the scenario specific parts since those won't be reused anywhere and would just waste space in the cache. So with this through cache you can put a in-memory cache in the front and the prepopulated persistent base run cache in the back. Any base run intermediate results will pull from back cache and anything specific to the scenario will store in front in-memory cache and be wiped after the scenario is finished running. You still want a in-memory cache at least because within a scenario run scenario-specific intermediate results can be reused.

##### Ephemeral Caching (Feature not currently available)
Currently, every intermediate node is cached when executed. This can be unideal if the results are large and take up cache space. In a future release of darl a provider can be marked such that the result will be uncached after its last dependent consumes its result. 

#### Alternative Provider Definitions
```
def A(ngn):
    b = ngn.B()
    c = ngn.C()
    ngn.collect()
    return b + c

class A:
    def __call__(self, ngn):
        b = ngn.B()
        c = ngn.C()
        ngn.collect()
        return b + c

def A(B, C):
    return B + C
```

All 3 of the above provider definitions are equivalent. You might notice that the last one looks significantly different, and it can be unclear how this is equivalent to the other two. In the case where a provider has a signature that does not start with `ngn`, the arguments to the function serve as the service names that will automatically be called and whose results will be passed to those arguemts. This only works if the services/providers do not take any function arguments. For example, `ngn.B()` can be invoked in this way, but `ngn.B(x)` cannot.

#### Alternative Service Invocations
```
ngn.A(99)
ngn['A'](99)
ngn.run_call(ngn.callkey.A(99))
```

All 3 of the above service call invocations are equivalent. The getitem method (i.e. `ngn[...]`) can also be used to pass dynamic string service names. For example:

```
class LineItem:
    def __init__(self, line_item, region):
        self.line_item = line_item
        self.region = region
        self.service_name = f'{line_item}For{region}'    # This instantiated classes default service name will be whatever is in this special attribute

    def __call__(self, ngn):
        data = ngn[f'DataFor{self.region}']()            # Dynamic string service call
        ngn.collect()
        return data[self.line_item]

def DataForUSA():
    return {'GDP': '99'}

ngn = Engine.create([
    LineItem('GDP', 'USA'),                              # Will be registered in engine with service name 'GDPForUSA'
    DataForUSA
])
print(ngn.GDPForUSA())  # -> 99
```

#### Code Hashing

The code hash of a provider automatically includes all source code of the function or class and any instance attributes. However, it does not include the source code of external non-provider functions that are used within the provider. A common pitfall is to update the source code of a non-provider function and expect the cache to invalidate for a provider where it is used. To make sure that the cache does automatically invalidate on code changes, these external functions need to be explicitly associated with the provider. Currently this association can be done by adding the function as an attribute on a provider class. (Currently there is no official mechanism to do this association with a function provider, only classes, a decorator will be added soon to associate external functions to be included in the code hash). Here is an example of how it can work with a class provider:

```
def some_function1():
    return 99

def some_function2():
    return 999

class A:
    include1 = some_function1()  # The name `include1` doesn't matter here, as long as the function is attached to some attribute
    include2 = some_function2()  # Currently each function needs to be attached to a separate attribute, rather than in a list, this will be fixed in future versions

    def __call__(self, ngn):
        a = some_function1()
        b = some_function2()
        return a + b
```

Now if you were to modify either of the `some_function*` functions provider A and everything downstream of it would invalidate.

(In a future version of darl the code hashing mechanism will be customizable to allow for hashing custom types or alternative user defined methodologies)

#### Unit Testing
Since darl providers are (or should be) deterministic and pure they lend themselves well to being unit-tested. This is very easy to do when the providers are using the service call as function arguments form discussed earlier, you can just treat them like regular functions:

```
def ServiceA(ServiceB, ServiceC):
    return ServiceB + ServiceC

assert ServiceA(1, 2) == 3
```
However, this gets a bit trickier when providers make service calls directly on the engine. Normally when a plain function call makes calls to another function you would have to mock the inner function call. With darl you can simply make use of service updates to achieve this effect. Darl provides a `pin` method to make this simpler.

```
from darl.call_key import CallKey

def ServiceA(ngn, x):
    b = ngn.ServiceB(x)
    c = ngn.ServiceC(x)
    ngn.collect()
    return b + c

def ServiceB(ngn, x):
    ...                                            # This snippet is a working example, since this provider implementation not actually used

ngn = Engine.create([ServiceA, ServiceB])
ngn = ngn.pin(ngn.callkey.ServiceB(1), 999)
ngn = ngn.pin(
    CallKey('ServiceC', {'x': 1}),                 # ngn.callkey only works if service exists on engine, otherwise create CallKey directly
    111
)

assert ngn.ServiceA(1) == 1110
```

#### Migration Testing
Besides unit-testing darl makes other types of testing much easier. For example, consider the task of migrating your python environment or libraries to a new upgraded version. Even though you would hope results from computational runs would be consistent across a migration like this, in practice it often is not. So, testing consistency of results across the original and updated environments is important. Without darl this would be done by running your model in the original env, running your model in the updated env and comparing the final results. However, this is not as simple and straightforward as it seems. Not yet even considering mismatched results, you cannot necessarily trust that a matching final result means that your migration is safe. A drift in intermediate results can still appear correct in the final result due to non-injective operations (operations that can produce the same result given different inputs). If at a later time one of these intermediate results were to pipe through a different downstream process you could unknowingly end up with incorrect results. Even in obvious cases where the final results are mismatched, it can be extremely difficult to identify where in the process the divergence(s) occurred. Darl makes testing for and identifying divergences like this easy using the trace/replay functionality, following these steps:

1. Load original env
2. Do a complete run, and save graph build id for later loading
3. Load new env
4. Load trace from original env run using graph build id
5. For each node in trace call trace.replay() and compare against trace.result (these can all be done in parallel)
6. Record all cases of mismatches and address with fixes

```
# In original env
from darl import Engine
from darl.cache import DiskCache

def A(B, C):
    import math
    return math.ceil(B) + C

def B(C):
    from random import random                     # Should never do this for real, just to force mismatch for demo purposes
    return C + random()                           # random() here illustrates env inconsistencies 

def C():
    return 99

cache = DiskCache('/tmp/darl_migration_example')
cache.purge()
ngn = Engine.create([A, B, C], cache=cache)
ngn.A()
graph_build_id = ngn.executed_graph_build_ids[-1]
print('Graph build id:', graph_build_id)


# In new env/new process (for this demo you can run all in same python process)

from darl.cache import DiskCache
from darl.trace import Trace

graph_build_id = graph_build_id                   # Pretend this was hardcoded, grabbed from result of print above
cache = DiskCache('/tmp/darl_migration_example')
trace = Trace.from_graph_build_id(graph_build_id, cache=cache)

for node in trace.graph.nodes.keys():             # Use your preferred method to parallelize this for loop
    trace_to_run = Trace.from_graph_build_id(graph_build_id, cache, node)
    new_result = trace_to_run.replay()
    old_result = trace_to_run.result
    service_name = trace_to_run.graph.nodes[node]['call_keys'][0].service_name
    if new_result == old_result:
        print(service_name, 'results match')
    else:
        print(service_name, 'results DO NOT match')

''' (results of prints above)       
C results match
B results DO NOT match
A results match
''';
```
You can see in this example that even though the final service A result matches, there is a mismatch in the intermediate B service, which is caught by the testing process. 

#### Parallel Compilation (Experimental)
It was previously mentioned that the compilation (aka graph build) step could take a significant amount of time depending on the complexity and amount of eager execution. In cases where refactoring to reduce these factors is not possible or feasible darl provides an alternative optimization through parallelizing the graph build. Currently, this feature is experimental and correctness is not guaranteed. Enabling parallel graph builds can be done like this:

```
from darl.graph_build.dask import DaskBuilder
from dask.distributed import Client

...

client = Client()
builder = DaskBuilder(client)
ngn = Engine.create(..., builder=builder)

ngn.Service()
```

#### Graph Inspection
There are many reasons one might want to inspect a computation graph before execution. One reason would be to simply see how much of the model is going to recompute without waiting for it to run through. For example:

```
def A(B, C):
    return B + C

def B():
    return 1

def C(D):
    return D + 1
    
def D():
    return 1

ngn = Engine.create([A, B, C, D])

ngn.A()
ngn = ngn.pin(ngn.callkey.C(), 999)

# At this point let's say we want to know what will execute
graph2 = ngn.compile(ngn.callkey.A())

print('Will execute the following nodes')
for node, data in graph2.nodes.items():
    if not data['from_cache_only']:
        print(data['call_keys'][0])

''' (result of prints above)
Will execute the following nodes
<CallKey(C: {}, ())>
<CallKey(A: {}, ())>
''';
```

##### Visualization
You can see a visual representation of the graph by doing the following (pygraphviz + networkx + matplotlib required):

```
def A(ngn):
    b = ngn.B()
    c1 = ngn.C(1)
    c2 = ngn.C(2)
    ngn.collect()
    return b + c1 + c2
    
def B(ngn):
    c = ngn.C(1)
    ngn.collect()
    return C + 1
    
def C(ngn, x):
    return x + 1
    
ngn = Engine.create([A, B, C])
G = ngn.compile(ngn.callkey.A())

G.visualize()
```
![viz_example.png](viz_example.png)

This graph visualization can be useful for smaller graphs, however, as the graph gets larger a visual representation becomes unwieldy and not as useful. In these cases it would be preferable to inspect the graph through programmatic means, for example, using the trace discussed in the previous "Tracing" section. It is possible to instantiate a trace from an unexecuted graph like so:

```
from darl.trace import Trace

trace = Trace(G)
```

While it's not currently provided directly in darl, it can also be useful to apply different visualization types (e.g. treemaps, flamegraphs, etc.), incorporating execution metadata, such as runtime for each node.

#### Miscellaneous
1. Darl can scale to hundreds of thousands or even millions of nodes. Performance is mainly limited by the execution engine. In large production systems parallel execution through Dask or Ray is recommended which can easily handle graphs this large. 
2. Darl engines, compilations and executions are thread-safe. You can use the same engine in different threads without concerns of executions interfering with each other.

#### Anti-patterns
1. Do not create and execute an engine within a provider. This can cause hard to identify inconsistencies in caching and results, since the cache and trace is not properly linked to the nested engine
2. Try/Except around service calls, this will not work as intended (see Error Handling section above for more details)
3. Relying heavily on inline execution and edge dependencies is often unneccessary and can be factored out to be defined in a more static fashion
4. In large models it's easy to accidentally create indirect dependencies on services that the dependent provider is not actually sensitive to, creating unneccesary invalidations from precached runs. These can be hard to identify and usually comes down to good naming of services to make these cases obvious.


## Demos

#### UI Fun
Here is an extremely minimal, just for fun, example of how to create a reactive UI using darl:

```
import time
from darl import Engine, value_hashed


def main_page(menu_bar, content, footer):
    return f'''
{menu_bar}
{content}
{footer}
'''

def _underline(string):
    return '\033[4m' + string + '\033[0m'

def menu_bar(all_options, selected_option):
    menu_string = ''
    for option in all_options:
        if option == selected_option:
            option = _underline(option)
        menu_string += f'{option}    '
    return menu_string

def all_options():
    return ['About', 'Products']

def selected_option():
    return 'About'

def about_page():
    return 'This is the about page'

def products_page():
    return 'This is the products page'

def content(ngn):
    selected_option = ngn.inline.selected_option()
    match selected_option:
        case 'About':
            ret_val = ngn.about_page()
        case 'Products':
            ret_val = ngn.products_page()
        case _:
            raise NotImplementedError
    ngn.collect()
    return ret_val

@value_hashed
def current_time():
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))

def footer(current_time):
    return f'Page last refreshed at: {current_time}'


if __name__ == '__main__':
    ngn = Engine.create([
        main_page,
        menu_bar,
        all_options,
        selected_option,
        about_page,
        products_page,
        content,
        current_time,
        footer,
    ])

    print(ngn.main_page())
    
    # In response to some event
    ngn = ngn.copy()  # to force recompute of value hashed results
    ngn.selected_option = 'Products'
    
    print(ngn.main_page())
```

While this is just a for fun proof of concept, a more practical integration in the UI space is using darl on the backend callbacks of plotly dash. By defining all callbacks to call services on an engine object you can recreate and debug any errors in your callbacks without needing to spin up a local plotly UI or trying to reproduce the conditions that led to the error.


## Alternatives
These libraries have similar APIs and functionality.

#### Apache Hamilton
GitHub: https://github.com/apache/hamilton

Docs: https://hamilton.apache.org

While Hamilton does not explicitly use the concepts of providers and services, the mental model can be used to describe Hamilton workflows. Some key points to consider: 
1. Hamilton exclusively uses the service calls as function arguments style of provider (e.g. `def ServiceA(serviceB, serviceC):`). While this style can get you far in building computational pipelines it does limit the ability to parameterize your models and simplify concepts like parallelization. For example Hamilton supports parallelization through special magic type-hints. Darl, by providing service calls through the ngn and allowing arguments to providers, allows independently parameterizing/parallelizing nodes using natural standard python constructs like a for loop.
2. Configuring different workflow graph structures in Hamilton requires the use of @config decorators (as opposed to darl's ngn.update/shock methods), which means that scenario definitions are tied to predefined source code definitions, which inhibits flexibility and agility of experimentation/scenario ideation.
3. In darl, caching is a first class citizen, meaning incremental computation is always possible in all modes of use. Hamilton requires specific plugins for caching that do not work with certain other plugins like parallel execution.
4. Hamilton does not support nested parallel execution, which darl does.
5. Darl scales to larger graphs more efficiently than Hamilton. Executing a graph of tens of thousands of low overhead nodes, darl profiled at over an order of magnitude faster. See code snippet below.
6. Darl provides scoped updates to allow more advanced scenario and diagnostic capabilities.
7. Currently, Hamilton's type checking system is more robust and mature than darl's.
8. To discuss other differences and advantages/disadvantages open a thread in the issues or discussion github page.

```
# ------------------------------------------------------------------------
# Hamilton
# ------------------------------------------------------------------------

from hamilton import driver
from hamilton.htypes import Parallelizable, Collect
from typing import Tuple, Dict

def product() -> Parallelizable[str]:          # Parallelizable creates a independent branch of nodes for each item in range
    return range(10000)
                                               # This specific example the product range could be treated as an atomic list
                                               # but treat independently in this demo to illustrate cases where operating on
                                               # the atomic group would either take too long or use too much memory

def product_data(product: str) -> int:
    return product + 1

def all_data_raw(product_data: Collect[int]) -> int:
    return sum(product_data)

dr = (
    driver.Builder()
    .with_modules(__import__('__main__'))
    .enable_dynamic_execution(allow_experimental_mode=True)
    .build()
)

dr.execute(['all_data_raw'])  # -> 50005000    # time this line (~55 sec)

# ------------------------------------------------------------------------
# Darl
# ------------------------------------------------------------------------

from darl import Engine

def ProductData(ngn, product):
    return product + 1

def AllDataRaw(ngn):
    res = [ngn.ProductData(product=i) for i in range(10000)]
    ngn.collect()
    return sum(res)
    
ngn = Engine.create([AllDataRaw, ProductData])

ngn.AllDataRaw()  # -> 50005000                # time this line (~1.5 sec)
    


```

#### fn_graph
Github: https://github.com/BusinessOptics/fn_graph

Docs: https://fn-graph.readthedocs.io/en/latest/usage.html

## Pronunciation
Like "Carl" with a "D".
