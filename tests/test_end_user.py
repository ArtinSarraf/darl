# TODO: break up into multiple files

import inspect

import pytest

from darl import value_hashed
from darl.cache import DictCache
from darl.call_key import CallKey
from darl.engine import Engine
from darl.error_handling import UpstreamException, ProviderException
from darl.trace import Trace


def assert_provider_exception_raise(func, exc):
    try:
        func()
    except ProviderException as e:
        match e.__cause__:
            case exc():
                assert True
            case _:
                assert False
    else:
        assert False


def test1():
    def A(B, C):
        return B + C

    def B(C):
        return C + 9

    def C():
        return 0.5

    ngn = Engine.create([A, B, C])
    assert ngn.A() == 10


def test2():
    def A(ngn):
        b = ngn['B']()
        c = ngn.C()
        ngn.collect()
        return b + c

    class B:
        def __call__(self, ngn):
            c = ngn.C()
            ngn.collect()
            return c + 9

    class C:
        def __call__(self, ngn):
            ngn.collect()
            return 0.5

    ngn = Engine.create([A, B(), C()])
    assert ngn.A() == 10


def test_set_service():
    ngn = Engine.create([])
    ngn.A = 1
    assert ngn.A() == 1


def test_call_key():
    def A(ngn):
        b = ngn.callkey.B()
        b = ngn.run_call(b)
        ngn.collect()
        return b + 1

    def B():
        return 1

    ngn = Engine.create([A, B])
    ngn.A()


def test_call_key_unused():
    def A(ngn):
        # previously failed because service mapping for args resolution during execution based only
        # on service dependencies, since this is never called here, it's not a dependency. resolved for now
        # by adding callkey services as a dependency (with the assumption that it will be an upstream dep
        # anyway since it will get passed and called upstream somewhere)
        b = ngn.callkey.B()
        c1 = ngn.C(b)

        d = ngn.callkey['D']()
        c2 = ngn.C(d)
        ngn.collect()
        return c1 + c2 + 1

    def B():
        return 1

    def D():
        return 2

    def C(ngn, x):
        if isinstance(x, CallKey):
            x = ngn.run_call(x)
        ngn.collect()
        return x + 1

    ngn = Engine.create([A, B, C, D])
    assert ngn.A() == 6


def test_inline():
    class A:
        def __call__(self, ngn):
            if ngn.inline.B() == 9:
                c = ngn.C()
                ngn.collect()
                return c
            else:
                ngn.collect()
                return 0
    def B():
        return 9

    def C():
        return 99

    ngn = Engine.create([A(), B, C])
    assert ngn.A() == 99


def test_inline_multi_call():
    counter = 0

    def A(B, C):
        return B + C

    def B(ngn):
        d = ngn.inline.D()
        ngn.collect()
        return d + 1

    def C(ngn):
        d = ngn.inline.D()
        ngn.collect()
        return d + 1

    def D():
        nonlocal counter
        counter += 1
        return 1

    ngn = Engine.create([A, B, C, D])
    assert ngn.A() == 4
    assert counter == 1  # should only be called once and pulled from cache other times


def test_inline_scoped():
    def A(B, C):
        return B + C

    def B(ngn):
        d = ngn.inline.D()
        if d != 1:
            raise ValueError()
        ngn.collect()
        return d + 1

    def C(ngn):
        d = ngn.inline.D()
        if d != 2:
            raise ValueError()
        ngn.collect()
        return d + 1

    def D():
        return 1

    def D2():
        return 2

    ngn = Engine.create([A, B, C, D])
    ngn = ngn.update({'D': D2}, scope=('C',))
    assert ngn.A() == 5


def test_inline_nested():
    def A(B):
        return B + 1

    def B(ngn):
        c = ngn.inline.C()
        if c > 0:
            x = 1
        else:
            x = 0
        ngn.collect()
        return c + x

    def C(D):
        return D + 1

    def D(ngn):
        e = ngn.inline.E()
        if e > 0:
            x = 1
        else:
            x = 0
        ngn.collect()
        return e + x

    def E():
        return 1

    ngn = Engine.create([A, B, C, D, E])
    assert ngn.A() == 5


def test_shock():
    def A():
        return 1

    ngn = Engine.create([A])
    ngn2 = ngn.shock('A', lambda x: x + 1)
    ngn3 = ngn2.shock('A', lambda x: x + 1)

    assert ngn.A() == 1
    assert ngn2.A() == 2
    assert ngn3.A() == 3


def test_shock_inplace():
    def A():
        return 1

    ngn = Engine.create([A])
    ngn2 = ngn.shock('A', lambda x: x + 1, inplace=True)
    ngn3 = ngn2.shock('A', lambda x: x + 1, inplace=True)

    assert ngn.A() == 3
    assert ngn2.A() == 3
    assert ngn3.A() == 3


def test_update():
    def A(B):
        return B + 1

    def B():
        return 1

    def B_prime():
        return 2

    ngn = Engine.create([A, B])
    ngn2 = ngn.update({'B': B_prime})

    assert ngn.A() == 2
    assert ngn2.A() == 3


def test_update_non_existing_provider():
    def A(B, C):
        return B + C

    def B():
        return 1

    def C():
        return 2

    ngn = Engine.create([A, B])
    ngn = ngn.update({'C': C})

    assert ngn.A() == 3


def test_update_list_of_providers():
    def A(B, C):
        return B + C

    def B():
        return 1

    class B_prime:
        def __init__(self):
            self.service_name = 'B'
        def __call__(self, ngn):
            return 2

    def C():
        return 2

    ngn = Engine.create([A, B])
    ngn = ngn.update([B_prime(), C])

    assert ngn.A() == 4


def test_update_existing_and_non_existing_providers():
    def A(B, C):
        return B + C

    def B():
        return 1

    def B_prime():
        return 2

    def C():
        return 2

    ngn = Engine.create([A, B])
    ngn = ngn.update({'B': B_prime, 'C': C})

    assert ngn.A() == 4

def test_update_inplace():
    def A(B):
        return B + 1

    def B():
        return 1

    def B_prime():
        return 2

    ngn = Engine.create([A, B])
    ngn2 = ngn.update({'B': B_prime}, inplace=True)

    assert ngn.A() == 3
    assert ngn2.A() == 3


def test_scoped_shock():
    def A(B, C):
        return B + C

    def B(D):
        return D + 1

    def C(D):
        return D + 2

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn2 = ngn.shock('D', lambda x: x + 1, scope=('B',))

    assert ngn.A() == 5
    assert ngn2.A() == 6


def test_scoped_update():
    def A(B, C):
        return B + C

    def B(D):
        return D + 1

    def C(D):
        return D + 2

    def D():
        return 1

    def D_prime():
        return 2

    ngn = Engine.create([A, B, C, D])
    ngn2 = ngn.update({'D': D_prime}, scope=('B',))

    assert ngn.A() == 5
    assert ngn2.A() == 6


@pytest.mark.xfail
def test_scoped_update_non_existing_service():
    def A(B, C):
        return B + C

    def B():
        return 1

    def C():
        return 2

    def C_prime():
        return 3

    ngn = Engine.create([A, B, C])
    try:
        ngn.update({'C': C_prime}, scope=('X',))
    except:
        assert True
    else:
        assert False


def test_args():
    def A(ngn, x):
        b = ngn.B(x)
        c1 = ngn.C()
        c2 = ngn.C(x=9)
        c3 = ngn.C(x=99)
        ngn.collect()
        return b + c1 + c2 + c3 + 1

    def B(ngn, x):
        ngn.collect()
        return x + 1

    def C(ngn, x=9):
        ngn.collect()
        return x + 1

    ngn = Engine.create([A, B, C])
    G = ngn.compile(ngn.callkey.A(1))
    C_kwargs = [G.nodes[x]['call_keys'][0].kwargs for x in G.nodes.keys() if G.nodes[x]['call_keys'][0].service_name == 'C']
    assert len(C_kwargs) == 2  # asserting that c1 and c2 are merged as one
    assert ngn.A(1) == 123


def test_dep_proxy():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        ngn.collect()
        return c + 1 + b

    def B():
        return 2

    def C(ngn, b):
        d = ngn.D(b)
        ngn.collect()
        b2 = b + 1
        return d + b2 + 1

    def D(ngn, b):
        ngn.collect()
        return b + 1

    ngn = Engine.create([A, B, C, D], allow_edge_dependencies=True)
    assert ngn.A() == 10


def test_no_edge_dependencies():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        ngn.collect()
        return b + c

    def B(ngn):
        c = ngn.C(1)
        ngn.collect()
        return c + 1

    def C(ngn, x):
        ngn.collect()
        return x + 1

    ngn = Engine.create([A, B, C])
    assert ngn.B() == 3

    try:
        ngn.A()
    except ValueError:
        assert True
    else:
        assert False


def test_dep_proxy_call_key_alternative():
    def A(ngn):
        b = ngn.callkey.B()
        c = ngn.C(b)
        if isinstance(b, CallKey):
            b = ngn.run_call(b)
        ngn.collect()
        return c + 1 + b

    def B():
        return 2

    def C(ngn, b):
        d = ngn.D(b)
        if isinstance(b, CallKey):
            b = ngn.run_call(b)
        ngn.collect()
        b2 = b + 1
        return d + b2 + 1

    def D(ngn, b):
        if isinstance(b, CallKey):
            b = ngn.run_call(b)
        ngn.collect()
        return b + 1

    ngn = Engine.create([A, B, C, D], allow_edge_dependencies=True)
    assert ngn.A() == 10


def test_call_key_multiple_from_different_scopes():
    def X1():
        return 1

    def X2():
        return 2

    def X3():
        return 3

    def Root(ngn):
        x = ngn.callkey.X()
        a = ngn.A(x)
        ngn.collect()
        return a

    def A(ngn, x):
        y = ngn.callkey.X()
        b = ngn.B(x, y)
        ngn.collect()
        return b

    def B(ngn, x, y):
        z = ngn.callkey.X()
        c = ngn.C(x, y, z)
        ngn.collect()
        return c

    def C(ngn, x, y, z):
        x = ngn.run_call(x)
        y = ngn.run_call(y)
        z = ngn.run_call(z)
        ngn.collect()
        return (x, y, z)

    ngn = Engine.create([Root, A, B, C])
    ngn = ngn.update({'X': X1}, scope=('Root',))
    ngn = ngn.update({'X': X2}, scope=('A',))
    ngn = ngn.update({'X': X3}, scope=('B',))
    assert ngn.Root() == (1, 2, 3)


def test_dep_proxy_multiple_from_different_scopes():
    def X1():
        return 1

    def X2():
        return 2

    def X3():
        return 3

    def Root(ngn):
        x = ngn.X()
        a = ngn.A(x)
        ngn.collect()
        return a

    def A(ngn, x):
        y = ngn.X()
        b = ngn.B(x, y)
        ngn.collect()
        return b

    def B(ngn, x, y):
        z = ngn.X()
        c = ngn.C(x, y, z)
        ngn.collect()
        return c

    def C(ngn, x, y, z):
        ngn.collect()
        return (x, y, z)

    ngn = Engine.create([Root, A, B, C], allow_edge_dependencies=True)
    ngn = ngn.update({'X': X1}, scope=('Root',))
    ngn = ngn.update({'X': X2}, scope=('A',))
    ngn = ngn.update({'X': X3}, scope=('B',))
    assert ngn.Root() == (1, 2, 3)


def test_dep_proxy_scoped():
    """
    dep proxy passed in should evaluate using scope of where dep proxy was created,
    not the scope it's in when passed into another service
    """
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        ngn.collect()
        return c + 1 + b

    def B():
        return 2

    def C(ngn, b):
        d = ngn.D(b)
        ngn.collect()
        b2 = b + 1
        return d + b2 + 1

    def D(ngn, b):
        b2 = ngn.B()
        ngn.collect()
        return b + 1 + b2

    def B2():
        return 3

    ngn = Engine.create([A, B, C, D], allow_edge_dependencies=True)
    assert ngn.A() == 12
    ngn = ngn.update({'B': B2}, scope=('D',))
    assert ngn.A() == 13


def test_dep_proxy_call_key_scoped_alternative():
    """
    call_key passed in should evaluate using scope of where call_key was created,
    not the scope it's in when passed into another service
    """
    def A(ngn):
        b = ngn.callkey.B()
        c = ngn.C(b)
        if isinstance(b, CallKey):
            b = ngn.run_call(b)
        ngn.collect()
        return c + 1 + b

    def B():
        return 2

    def C(ngn, b):
        d = ngn.D(b)
        if isinstance(b, CallKey):
            b = ngn.run_call(b)
        ngn.collect()
        b2 = b + 1
        return d + b2 + 1

    def D(ngn, b):
        b2 = ngn.B()
        if isinstance(b, CallKey):
            b = ngn.run_call(b)
        ngn.collect()
        return b + 1 + b2

    def B2():
        return 3

    ngn = Engine.create([A, B, C, D])
    assert ngn.A() == 12
    ngn = ngn.update({'B': B2}, scope=('D',))
    assert ngn.A() == 13


def test_dep_proxy_modified_fail():
    from darl.dep_proxy import DepProxyException

    def A(ngn):
        b = ngn.B()
        b = b + 1
        c = ngn.C(b)
        ngn.collect()
        return b + c + 1

    def B():
        return 1

    def C(ngn, x):
        ngn.collect()
        return x + 1

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    try:
        ngn.A()
    except DepProxyException:
        assert True
    else:
        assert False


def test_dep_proxy_modified_ok():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        b = b + 1
        ngn.collect()
        return b + c + 1

    def B():
        return 1

    def C(ngn, x):
        ngn.collect()
        return x + 1

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    assert ngn.A() == 5


def test_dep_proxy_modifications():
    def A(ngn):
        b = ngn.B()
        c = ngn.C()
        b1 = b.x(1).y(c) + 1
        b2 = 1 + b.xyz(9)
        ngn.collect()
        return b1 + b2 + c + 1

    def B():
        class Bresult:
            def __init__(self):
                self.val = 1
            def __add__(self, other):
                return self.val + other
            def __radd__(self, other):
                return self.val + other
            def x(self, i):
                self.val += i
                return self
            def y(self, i):
                self.val += i
                return self
            def xyz(self, i):
                self.val += i
                return self
        return Bresult()

    def C():
        return 2

    # non proxy modified, just to make sure behavior matches
    def A_prime(ngn):
        b = ngn.B()
        c = ngn.C()
        ngn.collect()
        b1 = b.x(1).y(c) + 1  # 4
        b2 = 1 + b.xyz(9)
        return b1 + b2 + c + 1

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    assert ngn.A() == 22
    ngn = Engine.create([A, B, C], allow_edge_dependencies=True).update({'A': A_prime})
    assert ngn.A() == 22


def test_proxy_operation_forbidden():
    from darl.dep_proxy import DepProxyException

    def Bad1(ngn):
        a = ngn.A()
        if a:
            raise RuntimeError('this should never be reached')
        ngn.collect()
        return 99

    def Bad2(ngn):
        a = ngn.A()
        for i in a:
            raise RuntimeError('this should never be reached')
        ngn.collect()
        return 99

    def A():
        return 99

    ngn = Engine.create([Bad1, Bad2, A])
    try:
        ngn.Bad1()
    except DepProxyException:
        assert True
    else:
        assert False

    try:
        ngn.Bad2()
    except DepProxyException:
        assert True
    else:
        assert False


def test_failure():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    def B():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B])
    assert_provider_exception_raise(ngn.Root, RuntimeError)


def test_catch_no_error():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 999
        return b + 1

    def B():
        return 1

    ngn = Engine.create([Root, A, B])
    assert ngn.Root() == 3


def test_catch_error_match_case():
    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        match b:
            case ngn.error(RuntimeError()):
                b = 1
            case ngn.error(ValueError()):
                b = 2
            case ngn.error():
                b = 3
        return b

    class B:
        def __init__(self, error=None):
            self.error = error

        def __call__(self):
            if self.error is None:
                return 0
            elif self.error == 'RuntimeError':
                raise RuntimeError()
            elif self.error == 'ValueError':
                raise ValueError()
            elif self.error == 'TypeError':
                raise TypeError()
            else:
                raise NotImplementedError()

    ngn = Engine.create([A, B()])
    assert ngn.A() == 0
    ngn = Engine.create([A, B('RuntimeError')])
    assert ngn.A() == 1
    ngn = Engine.create([A, B('ValueError')])
    assert ngn.A() == 2
    ngn = Engine.create([A, B('TypeError')])
    assert ngn.A() == 3


def test_catch_error_unhandled_error():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        match b:
            case ngn.error(ValueError()):
                b = 999
            case ngn.error():
                raise b.error
        return b + 1

    def B(C):
        return C

    def C():
        error_raiser()

    def error_raiser():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B, C])
    assert_provider_exception_raise(ngn.Root, RuntimeError)


def test_catch_error_top_level_match():
    def A():
        raise RuntimeError()

    ngn = Engine.create([A])
    a = ngn.catch.A()

    x = 0
    match a:
        case ngn.error(RuntimeError()):
            x = 1
    assert x == 1

    match a:
        case ngn.error():
            x = 2
    assert x == 2


def test_catch_errors_direct():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        match b:
            case ngn.error(RuntimeError()):
                b = 999
        return b + 1

    def B():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B])
    assert ngn.Root() == 1001


def test_catch_errors_nested():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 999
        return b + 1

    def B(C):
        return C + 1

    def C():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B, C])
    assert ngn.Root() == 1001


def test_catch_errors_multi_nested():
    from darl.error_handling import ErrorSentinel

    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 999
        return b + 1

    def B(ngn):
        c = ngn.catch.C()
        ngn.collect()
        if isinstance(c, ErrorSentinel):
            c = 9
        return c + 1

    def C():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B, C])
    assert ngn.Root() == 12


def test_catch_errors_multi_call():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.B()
        c = ngn.catch.C()
        ngn.collect()
        if isinstance(c, ngn.error):
            c = 999
        return b + c

    def B(ngn):
        c = ngn.catch.C()
        ngn.collect()
        if isinstance(c, ngn.error):
            c = 999
        return c + 1

    def C(D):
        return D + 1

    def D():
        raise ValueError('error in D')

    ngn = Engine.create([Root, A, B, C, D])
    assert ngn.Root() == 2000


def test_catch_error_not_in_all_places():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.B()
        c = ngn.catch.C()
        ngn.collect()
        if isinstance(c, ValueError):
            pass
        return b + c

    def B(C):  # should fail since C is not catched here
        return C + 1

    def C(D):
        return D + 1

    def D():
        raise ValueError('error in D')

    ngn = Engine.create([Root, A, B, C, D])
    try:
        ngn.Root()
    except UpstreamException:
        assert True
    else:
        assert False


def test_catch_error_not_in_all_places_no_error():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.B()
        c = ngn.catch.C()
        ngn.collect()
        if isinstance(c, ValueError):
            pass
        return b + c

    def B(C):  # should fail since C is not catched here
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([Root, A, B, C, D])
    assert ngn.Root() == 6


def test_catch_error_root():
    from darl.error_handling import ErrorSentinel

    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    def B():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B])
    assert isinstance(ngn.catch.Root(), ErrorSentinel)


def test_catch_error_root_runcall():
    from darl.error_handling import ErrorSentinel

    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    def B():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B])
    assert isinstance(ngn.catch.run_call(ngn.callkey.Root()), ErrorSentinel)


def test_catch_error_runcall():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.catch.run_call(ngn.callkey.B())
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 999
        return b + 1

    def B(C):
        return C + 1

    def C():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B, C])
    assert ngn.Root() == 1001


def test_catch_error_callkey_arg_runcall():
    def A(ngn):
        b = ngn.callkey.B()
        c = ngn.C(b)
        b = ngn.catch.run_call(b)
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + c

    def B():
        raise ValueError('error in B')

    def C(ngn, b):
        if isinstance(b, CallKey):
            b = ngn.catch.run_call(b)
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + 1

    ngn = Engine.create([A, B, C])
    assert ngn.A() == 199


def test_catch_error_other_upstream_uncaught_errors():
    def A(ngn):
        b = ngn.catch.B()
        c = ngn.C()
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + c

    def B():
        raise RuntimeError('error 1')

    def C():
        raise RuntimeError('error 2')

    ngn = Engine.create([A, B, C])
    try:
        ngn.A()
    except ProviderException as e:
        assert e.__cause__.args[0] == 'error 2'
    else:
        assert False


def test_catch_error_dep_proxy():
    def A(ngn):
        b = ngn.catch.B()
        c = ngn.C(b)
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + c

    def B():
        raise ValueError('error in B')

    def C(ngn, b):
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + 1

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    assert ngn.A() == 199


def test_catch_error_dep_proxy_fail():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + c

    def B():
        raise ValueError('error in B')

    def C(ngn, b):
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + 1

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    assert_provider_exception_raise(ngn.A, ValueError)


def test_catch_error_raise_in_catching_provider():
    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    def B(ngn):
        c = ngn.catch.C()
        ngn.collect()
        raise RuntimeError('error 2')

    def C():
        raise RuntimeError('error 1')

    ngn = Engine.create([A, B, C])
    try:
        ngn.A()
    except ProviderException as e:
        assert e.__cause__.args[0] == 'error 2'
    else:
        assert False


def test_catch_error_fail_in_non_catch_branch():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.catch.B()
        c = ngn.C()
        ngn.collect()
        if isinstance(b, ngn.error):
            b = 99
        return b + c

    def B(D):
        return D + 1

    def C(E):
        return E + 1

    def D():
        raise RuntimeError()

    def E():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B, C, D, E])
    assert_provider_exception_raise(ngn.Root, RuntimeError)  # should be the actual error raised, since no catch for the C branch


def test_catch_and_no_catch_same_call():
    def Root(A):
        return A + 1

    def A(ngn):
        b1 = ngn.catch.B()
        b2 = ngn.B()
        ngn.collect()
        if isinstance(b1, ngn.error):
            b1 = 999
        return b1 + b2 + 1

    def B():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B])
    try:
        ngn.Root()
    except ProviderException as e:
        assert 'duplicate service calls are being made, one with .catch and one without.' in e.__cause__.args[0]
    else:
        assert False


def test_catch_error_from_value_hashed():
    """
    Cannot catch error behind value hashed
    TODO: should we allow this?
    """
    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    def B(ngn):
        c = ngn.catch.C()
        ngn.collect()
        if isinstance(c, ngn.error):
            c = 99
        return c

    @value_hashed
    def C(D):
        return D + 1

    def D():
        raise RuntimeError('error 1')

    @value_hashed
    def C_direct_fail():
        raise RuntimeError('error 2')

    ngn = Engine.create([A, B, C, D])
    assert_provider_exception_raise(ngn.A, RuntimeError)

    ngn = ngn.update({'C': C_direct_fail})
    assert_provider_exception_raise(ngn.A, RuntimeError)


def test_catch_error_in_value_hashed():
    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    @value_hashed
    def B(ngn):
        c = ngn.catch.C()
        ngn.collect()
        if isinstance(c, ngn.error):
            c = 99
        return c

    def C():
        raise RuntimeError('error 1')

    ngn = Engine.create([A, B, C])
    assert ngn.A() == 100


@pytest.mark.xfail
def test_inline_call_on_error_sentinel():
    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        match b:
            case ngn.error(RuntimeError()):
                b = 999
        return b + 1

    def B(ngn):
        c = ngn.inline.C()  # error gets raised here instead of short-circuiting from upstream catch
        ngn.collect()
        return c + 1

    def C(ngn):
        raise RuntimeError()

    ngn = Engine.create([A, B, C])
    assert ngn.A() == 1000


def test_value_hashed_provider():
    from darl.provider import value_hashed
    from darl.cache import DictCache

    x = iter([1, 2, 1])
    run_counter = 0

    @value_hashed
    def token():
        a = next(x)
        return a

    def data(token):
        nonlocal run_counter
        run_counter += 1
        return token + 1

    cache = DictCache()
    ngn = Engine.create([data, token], cache=cache)
    ngn.data()
    ngn = Engine.create([data, token], cache=cache)
    ngn.data()
    ngn = Engine.create([data, token], cache=cache)
    ngn.data()
    assert run_counter == 2


def test_value_hashed_provider_single_eng_instantiation():
    # for a single ngn instantiation the token should not be reevaluated
    from darl.provider import value_hashed

    x = iter([1, 2, 1])
    run_counter = 0
    token_counter = 0

    @value_hashed
    def token():
        nonlocal token_counter
        token_counter += 1
        a = next(x)
        return a

    def data(token):
        nonlocal run_counter
        run_counter += 1
        return token + 1

    ngn = Engine.create([data, token])
    ngn.data()
    ngn.data()
    ngn.data()
    assert run_counter == 1
    assert token_counter == 1


def test_value_hashed_clone():
    # for a clone the token should not be reevaluated
    from darl.provider import value_hashed

    x = iter([1, 2, 1])
    run_counter = 0
    token_counter = 0

    @value_hashed
    def token():
        nonlocal token_counter
        token_counter += 1
        a = next(x)
        return a

    def data(token):
        nonlocal run_counter
        run_counter += 1
        return token + 1

    ngn = Engine.create([data, token])
    ngn.data()
    ngn = ngn.clone()
    ngn.data()
    ngn.data()
    assert run_counter == 1
    assert token_counter == 1


def test_value_hashed_copy():
    # for a copy the token should be reevaluated
    from darl.provider import value_hashed

    x = iter([1, 2, 1])
    run_counter = 0
    token_counter = 0

    @value_hashed
    def token():
        nonlocal token_counter
        token_counter += 1
        a = next(x)
        return a

    def data(token):
        nonlocal run_counter
        run_counter += 1
        return token + 1

    ngn = Engine.create([data, token])
    ngn.data()
    ngn = ngn.copy()
    ngn.data()
    ngn.data()
    assert run_counter == 2
    assert token_counter == 2


def test_value_hashed_update():
    # for a update the token should not be reevaluated, since clone is used under the hood
    # can use ngn.copy().update(...) if you want reevaluation
    from darl.provider import value_hashed

    x = iter([1, 2, 1])
    run_counter = 0
    token_counter = 0

    @value_hashed
    def token():
        nonlocal token_counter
        token_counter += 1
        a = next(x)
        return a

    class data:
        def __init__(self, dummy):
            self.dummy = dummy

        def __call__(self, token):
            nonlocal run_counter
            run_counter += 1
            return token + 1

    ngn = Engine.create([data(1), token])
    ngn.data()
    ngn = ngn.update({'data': data(2)})
    ngn.data()
    assert run_counter == 2
    assert token_counter == 1

    ngn = ngn.copy().update({'data': data(3)})
    ngn.data()
    assert run_counter == 3
    assert token_counter == 2


def test_value_hashed_provider_with_args():
    from darl.provider import value_hashed
    from darl.cache import DictCache

    x = iter([1, 2, 1])
    run_counter = 0

    @value_hashed
    def token(ngn, i):
        ngn.collect()
        return i

    def data(ngn, i):
        t = ngn.token(i)
        ngn.collect()
        nonlocal run_counter
        run_counter += 1
        return t + 1

    cache = DictCache()
    ngn = Engine.create([data, token], cache=cache)
    ngn.data(next(x))
    ngn = Engine.create([data, token], cache=cache)
    ngn.data(next(x))
    ngn = Engine.create([data, token], cache=cache)
    ngn.data(next(x))
    assert run_counter == 2


def test_value_hashed_provider_with_deps():
    from darl.provider import value_hashed
    from darl.cache import DictCache

    x = iter([1, 2, 1])
    run_counter = 0

    def token_A(ngn, y):
        b = ngn.token_B()
        ngn.collect()
        return b + y

    def token_B(ngn):
        tc = ngn.token_C(99)
        ngn.collect()
        return tc

    def token_C(ngn, z):
        ngn.collect()
        return z + 1

    @value_hashed
    def token(ngn, y, z, i=11):
        a = ngn.token_A(y)
        ngn.collect()
        return a + z + i

    def data(ngn, y):
        t = ngn.token(y, 10)
        ngn.collect()
        nonlocal run_counter
        run_counter += 1
        return t + 1

    cache = DictCache()
    ngn = Engine.create([data, token, token_A, token_B, token_C], cache=cache)
    ngn.data(next(x))
    ngn = Engine.create([data, token, token_A, token_B, token_C], cache=cache)
    ngn.data(next(x))
    ngn = Engine.create([data, token, token_A, token_B, token_C], cache=cache)
    ngn.data(next(x))
    assert run_counter == 2


def test_value_hashed_terminal_node():
    # for value hashed providers with deps, make sure graph terminates at value hashed node
    def A(token):
        return token + 1

    @value_hashed
    def token(tokenA):
        return tokenA + 1

    def tokenA():
        return 1

    ngn = Engine.create([A, token, tokenA])
    G = ngn.compile(ngn.callkey.A())
    assert sorted(sum([[ck.service_name for ck in d['call_keys']] for d in G.nodes.values()], [])) == ['A', 'token']


def test_value_hashed_provider_scope_updated():
    # e.g. make sure that value hashed run starts at right scope ad picks up correct changes
    from darl.provider import value_hashed

    @value_hashed
    def token_A():
        return 99

    @value_hashed
    def token(ngn):
        tokenA = ngn.token_A()
        ngn.collect()
        return tokenA

    def data1(ngn):
        t = ngn.token()
        ngn.collect()
        return t

    def data2(ngn):
        t = ngn.token()
        ngn.collect()
        return t

    def data(data1, data2):
        return data1 + data2

    ngn = Engine.create([token_A, token, data, data1, data2])
    ngn = ngn.shock('token_A', lambda x: x + 1, scope=('data2',))
    assert ngn.data() == 199


def test_value_hashed_provider_multi_call():
    from darl.provider import value_hashed

    x = iter([1, 2, 3])

    def token_A():
        return next(x)

    @value_hashed
    def token(token_A):
        return token_A

    def A(B, C):
        return B + C

    def B(token):
        return token

    def C(token):
        return token

    ngn = Engine.create([A, B, C, token, token_A])
    assert ngn.A() == 2
    assert ngn.A() == 2
    ngn2 = ngn.clone()
    assert ngn2.A() == 2
    ngn = Engine.create([A, B, C, token, token_A])
    assert ngn.A() == 4
    assert ngn.A() == 4
    ngn2 = ngn.clone()
    assert ngn2.A() == 4
    ngn = ngn.copy()
    assert ngn.A() == 6
    assert ngn.A() == 6
    ngn2 = ngn.clone()
    assert ngn2.A() == 6


def test_non_fully_resolved_scope():
    def A(ngn):
        b = ngn.B()
        c = ngn.C()
        ngn.collect()
        return b + c + 1

    def B(ngn) -> int:
        c = ngn.C()
        ngn.collect()
        return c + 1

    def C():
        return 99

    ngn = Engine.create([A, B, C])
    ngn = ngn.shock('C', lambda x: x + 1, ('B', 'X'))  # this shock should never materialize
    assert ngn.A() == 200


def test_irrelevant_scope_fully_resolved_scope():
    def A(ngn):
        b = ngn.B()
        c = ngn.C()
        ngn.collect()
        return b + c + 1

    def B(ngn) -> int:
        c = ngn.C()
        ngn.collect()
        return c + 1

    def C():
        return 99

    def Z():
        return 1

    ngn = Engine.create([A, B, C, Z])
    ngn = ngn.shock('Z', lambda x: x + 1, ('B',))  # nothing in A, B, C affected by this
    assert ngn.A() == 200


def test_type_annotation():
    def A(ngn):
        b = ngn.type[int].B()
        c = ngn.C()
        ngn.collect()
        return b + c + 1

    def B() -> int:
        return 99

    def C(ngn):
        b = ngn.type[str].B()  # for now incorrect type hint shouldn't cause failure
        ngn.collect()
        return b + 1

    ngn = Engine.create([A, B, C])
    # this shock should never materialize, just here to cause
    # some divergence in graph build scopes and make sure things
    # still resolve properly in terms of tracking type hints (mainly
    # the storing of type hints by deps hash (not scoped hash as it was previously
    # which caused issues)
    ngn = ngn.shock('B', lambda x: x + 1, ('C', 'X'))
    G = ngn.compile(ngn.callkey.A())

    A_data, = [v for v in G.nodes.values() if v['call_keys'][0].service_name == 'A']
    B_data, = [v for v in G.nodes.values() if v['call_keys'][0].service_name == 'B']
    B_ck = B_data['cache_key']
    C_data, = [v for v in G.nodes.values() if v['call_keys'][0].service_name == 'C']

    assert A_data['return_type'] is inspect._empty
    assert B_data['return_type'] == int
    assert A_data['deps_expected_types_by_cache_key'][B_ck] == int
    assert C_data['deps_expected_types_by_cache_key'][B_ck] == str

    assert ngn.A() == 200


def test_type_annotation_service_args():
    def A(B: int, C):
        return B + C + 1

    def B() -> int:
        return 1

    def C() -> int:
        return 1

    ngn = Engine.create([A, B, C])

    G = ngn.compile(ngn.callkey.A())

    A_data, = [v for v in G.nodes.values() if v['call_keys'][0].service_name == 'A']
    B_data, = [v for v in G.nodes.values() if v['call_keys'][0].service_name == 'B']
    B_ck = B_data['cache_key']

    assert A_data['return_type'] is inspect._empty
    assert B_data['return_type'] == int
    assert A_data['deps_expected_types_by_cache_key'][B_ck] == int
    assert len(A_data['deps_expected_types_by_cache_key']) == 1

    assert ngn.A() == 3


def test_enforce_types_success():
    def A(B: int, C: int) -> int:
        return B + C + 1

    def B() -> int:
        return 1

    def C() -> int:
        return 1

    ngn = Engine.create([A, B, C], enforce_types=True)
    ngn.compile(ngn.callkey.A())

    def A_prime(B, C) -> int:
        return B + C + 1

    ngn = ngn.update({'A': A_prime})
    ngn.compile(ngn.callkey.A())


def test_enforce_types_shock():
    def A(B: int, C: int) -> int:
        return B + C + 1

    def B() -> int:
        return 1

    def C() -> int:
        return 1

    ngn = Engine.create([A, B, C], enforce_types=True)
    ngn1 = ngn.shock('B', lambda x: x + 1, return_type=int)
    ngn1.compile(ngn.callkey.A())

    ngn2 = ngn.shock('B', lambda x: x + 1, return_type=str)
    try:
        ngn2.compile(ngn.callkey.A())
    except TypeError:
        assert True
    else:
        assert False

    ngn3 = ngn.shock('B', lambda x: x + 1)  # no return provided, can't be auto-inferred for now
    try:
        ngn3.compile(ngn.callkey.A())
    except TypeError:
        assert True
    else:
        assert False


def test_enforce_types_fail():
    def A(B: int, C: int) -> int:
        return B + C + 1

    def B() -> str:
        return 'a'

    def C() -> int:
        return 1

    ngn = Engine.create([A, B, C], enforce_types=True)
    try:
        ngn.compile(ngn.callkey.A())
    except TypeError:
        assert True
    else:
        assert False

    def B_prime():  # if downstream has expected type, then the implementing provider needs to have a return type
        return 1

    ngn = ngn.update({'B': B_prime})
    try:
        ngn.compile(ngn.callkey.A())
    except TypeError:
        assert True
    else:
        assert False


def test_enforce_types_made_up_types():
    def A(B: 'the value 1', C: int) -> int:
        return B + C + 1

    def B() -> 'the value 1':
        return 1

    def C() -> int:
        return 2

    ngn = Engine.create([A, B, C], enforce_types=True)
    ngn.compile(ngn.callkey.A())


def test_type_annotation_top_level():
    def A(B: int) -> int:
        return B + 1

    def B() -> int:
        return 1

    ngn = Engine.create([A, B], enforce_types=True)
    ngn.compile(ngn.callkey.A())

    assert ngn.type[int].A() == 2
    assert ngn.type[str].A() == 2  # no actual type checking happens for now on top level

def test_code_hashing():
    from darl.helpers import hash_callable

    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    a1 = hash_callable(A)

    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    a2 = hash_callable(A)

    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 2

    a3 = hash_callable(A)

    assert a1 == a2
    assert a1 != a3

    class B:
        z = 99

        def __init__(self):
            self.y = 1
            self.f = lambda x: x + 1

        def __call__(self, ngn):
            b = ngn.B()
            ngn.collect()
            return self.method1(b) + self.method2(1) + self.z + self.f(self.y)

        def method1(self, x):
            return x + 1

        def method2(self, x):
            return x + 2

    b1 = hash_callable(B())

    class C(B):
        def method2(self, x):
            return x + 3

    c1 = hash_callable(C())

    class B:
        z = 99

        def __init__(self):
            self.y = 1
            self.f = lambda x: x + 1

        def __call__(self, ngn):
            b = ngn.B()
            ngn.collect()
            return self.method1(b) + self.method2(1) + self.z + self.f(self.y)

        def method1(self, x):
            return x + 1

        def method2(self, x):
            return x + 2

    b2 = hash_callable(B())

    class C(B):
        def method2(self, x):
            return x + 3

    c2 = hash_callable(C())

    class B:
        z = 99

        def __init__(self):
            self.y = 1
            self.f = lambda x: x + 1

        def __call__(self, ngn):
            b = ngn.B()
            ngn.collect()
            return self.method1(b) + self.method2(1) + self.z + self.f(self.y)

        def method1(self, x):
            return x + 4

        def method2(self, x):
            return x + 2

    b3 = hash_callable(B())

    class C(B):
        def method2(self, x):
            return x + 3

    c3 = hash_callable(C())

    assert b1 == b2
    assert c1 == c2
    assert b1 != b3
    assert c1 != c3


def test_code_hashing_recursive_provider():
    from darl.helpers import hash_callable

    class A:
        a = 1
        def __call__(self, A, B):
            return self.method(A + B)
        def method(self, x):
            return x + self.a

    class B(A):
        def method(self, x):
            return x + self.a + 1

    class C:
        def __init__(self, p):
            self.p = p
        def __call__(self, A, B):
            return self.p(A, B)

    a1 = hash_callable(C(B()))
    a2 = hash_callable(C(B()))
    b = hash_callable(C(lambda A, B: A * B))

    assert a1 == a2
    assert a1 != b


class _HashPickleTestProvider:
    def __call__(self):
        return 3


def test_code_hashing_pickle_dumps():
    # was discovered that pickle.dumps can inplace add a __slotnames__ attribute to an obejct messing up the hash
    import pickle
    from darl.helpers import hash_callable

    hash1 = hash_callable(_HashPickleTestProvider())
    pickle.dumps(_HashPickleTestProvider())
    hash2 = hash_callable(_HashPickleTestProvider())

    assert hash1 == hash2


def test_code_hashing_default_binding():
    # would this ability ever be used for anything other than testing
    from darl.helpers import hash_callable

    funcs = {}
    for name, val in [('A', 1), ('B', 2), ('C', 3)]:
        def func(ngn, x, y=val):
            return x + y
        funcs[name] = func

    ngn = Engine.create(funcs)
    assert ngn.A(1) == 2
    assert ngn.B(1) == 3
    assert ngn.C(1) == 4

    hashA = hash_callable(funcs['A'])
    hashB = hash_callable(funcs['B'])
    hashC = hash_callable(funcs['C'])

    assert hashA != hashB
    assert hashA != hashC
    assert hashB != hashC


def test_code_hashing_closure():
    # would this ability ever be used for anything other than testing
    from darl.helpers import hash_callable

    def func_factory(y_val):
        def func(ngn, x, y=y_val):
            return x + y
        return func

    funcs = {}
    for name, val in [('A', 1), ('B', 2), ('C', 3)]:
        funcs[name] = func_factory(val)

    ngn = Engine.create(funcs)
    assert ngn.A(1) == 2
    assert ngn.B(1) == 3
    assert ngn.C(1) == 4

    hashA = hash_callable(funcs['A'])
    hashB = hash_callable(funcs['B'])
    hashC = hash_callable(funcs['C'])

    assert hashA != hashB
    assert hashA != hashC
    assert hashB != hashC


def test_code_hashing_external_function():
    from darl.helpers import hash_callable

    def some_func():
        return 99

    class A:
        def __call__(self, ngn):
            return some_func()

    hash1 = hash_callable(A())

    def some_func():
        return 100

    class A:
        def __call__(self, ngn):
            return some_func()

    hash2 = hash_callable(A())

    assert hash1 == hash2

    def some_func():
        return 99

    class A:
        _some_func = some_func
        def __call__(self, ngn):
            return some_func()

    hash3 = hash_callable(A())

    def some_func():
        return 100

    class A:
        _some_func = some_func
        def __call__(self, ngn):
            return some_func()

    hash4 = hash_callable(A())

    assert hash3 != hash4


def test_catch_error_across_runs():
    def Root(A):
        return A + 1

    class A:
        def __init__(self, error):
            self.error = error

        def __call__(self, ngn):
            b = ngn.catch.B()
            c = ngn.C()
            ngn.collect()
            if self.error:
                raise RuntimeError('error 2')
            else:
                if isinstance(b, ngn.error):
                    b = 99
                return b + c

    def B(D):
        return D + 1

    def C():
        return 99

    def D():
        raise RuntimeError('error 1')

    ngn = Engine.create([Root, A(error=True), B, C, D])
    assert_provider_exception_raise(ngn.Root, RuntimeError)
    ngn = Engine.create([Root, A(error=False), B, C, D], cache=ngn.cache)
    G = ngn.compile(ngn.callkey.Root())

    computed = []
    for node_data in G.nodes.values():
        if not node_data['from_cache_only']:
            computed.append(node_data['call_keys'][0].service_name)
    assert sorted(computed) == ['A', 'Root']


def test_conflicting_provider_hash():
    def X():
        return 1

    def A(B, C):
        return B + C

    ngn = Engine.create([A])
    ngn = ngn.update({'B': X, 'C': X})
    assert ngn.A() == 2


def test_subclass_provider():
    class Base:
        def __init__(self, line_item, space):
            self.service_name = f'{line_item}{space}'
            self.line_item = line_item
            self.space = space

        def __call__(self, ngn):
            ngn.collect()
            return {
                ('LineItemA', 'Realized'): 99,
            }[(self.line_item, self.space)]

    class Realized(Base):
        def __init__(self, line_item):
            super().__init__(line_item, 'Realized')

    class LineItemARealizedProvider(Realized):
        def __init__(self):
            super().__init__('LineItemA')

    ngn = Engine.create([LineItemARealizedProvider()])
    assert ngn.LineItemARealized() == 99


def test_subclass_provider_extra_service_calls():
    class Base0:
        def __call__(self, ngn):
            a = ngn.A()
            ngn.collect()
            return a

    class Base1(Base0):
        def __call__(self, ngn):
            b = ngn.B()
            # extra calls need to be before the super call since
            # ngn.collect() gets called in there and raises the exit exception
            # this is not necessary if super method decorated with embeddable
            res = super().__call__(ngn)
            ngn.collect()
            return res * b

    class Main(Base1):
        def __call__(self, ngn):
            c = ngn.C()
            # extra calls need to be before the super call since
            # ngn.collect() gets called in there and raises the exit exception
            # this is not necessary if super method decorated with embeddable
            res = super().__call__(ngn)
            ngn.collect()
            return res * c

    def A():
        return 3

    def B():
        return 3

    def C():
        return 3

    ngn = Engine.create([A, B, C, Main()])
    assert ngn.Main() == 27


def test_mode_flags():
    def non_provider_func1(ngn):
        x = ngn.X()
        if ngn.is_collect_mode:
            return
        return x + 1

    def non_provider_func2(ngn):
        x = ngn.X()
        if ngn.is_execution_mode:
            return x + 1
        else:
            return

    def A(ngn):
        b = ngn.B()
        x1 = non_provider_func1(ngn)
        x2 = non_provider_func2(ngn)
        ngn.collect()
        return b + x1 + x2

    def B():
        return 96

    def X():
        return 1

    ngn = Engine.create([A, B, X])
    assert ngn.A() == 100
    # top level ngn should always be in execution mode
    assert ngn.is_execution_mode
    assert not ngn.is_collect_mode


def test_embeddable_func():
    from darl import embeddable

    @embeddable
    def non_provider_func(ngn):
        x = ngn.X()
        ngn.collect()
        return x + 1

    def A(ngn):
        b = ngn.B()
        x = non_provider_func(ngn)
        c = ngn.C()  # have one call before and after non_provider_func for more complete testing
        ngn.collect()
        return b + c + x

    def B():
        return 97

    def C():
        return 1

    def X():
        return 1

    ngn = Engine.create([A, B, C, X])
    assert ngn.A() == 100


def test_embeddable_func_not_embedded():
    from darl import embeddable

    @embeddable
    def non_provider_func(ngn):
        x = ngn.X()
        ngn.collect()
        return x + 1

    def X():
        return 99

    ngn = Engine.create([X])
    assert non_provider_func(ngn) == 100


def test_embeddable_super_method():
    from darl import embeddable

    class Base0:
        @embeddable
        def __call__(self, ngn):
            x = self.ngn_calling_method_X(ngn)
            a = ngn.A()  # put after method_X to make sure collect properly handled
            ngn.collect()
            return a * x

        @embeddable
        def ngn_calling_method_X(self, ngn):
            e = ngn.E()
            ngn.collect()
            return e * 1

    @embeddable
    class Base1(Base0):
        def __call__(self, ngn):
            res = super().__call__(ngn)
            y = self.ngn_calling_method_Y(ngn)
            b = ngn.B()  # put after method_Y to make sure collect properly handled
            ngn.collect()
            return res * b * y

        def ngn_calling_method_Y(self, ngn):
            d = ngn.D()
            ngn.collect()
            return d * 1

    class Main(Base1):
        def __call__(self, ngn):
            res = super().__call__(ngn)
            c = ngn.C()
            ngn.collect()
            return res * c

    def A():
        return 3

    def B():
        return 3

    def C():
        return 3

    def D():
        return 3

    def E():
        return 3

    ngn = Engine.create([A, B, C, D, E, Main()])
    assert ngn.Main() == 243


def test_dep_proxy_nested_type_1():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        d = ngn.D(c)
        ngn.collect()
        return b + c + d

    def B():
        return 1

    def C(ngn, b):
        ngn.collect()
        return b + 1

    def D(ngn, c):
        ngn.collect()
        return c + 1

    ngn = Engine.create([A, B, C, D], allow_edge_dependencies=True)
    assert ngn.A() == 6


def test_dep_proxy_nested_type_2():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        ngn.collect()
        return c + 1

    def B():
        return 1

    def C(ngn, b):
        d = ngn.D(b)
        e = ngn.E(d)
        ngn.collect()
        return e + 1

    def D(ngn, b):
        ngn.collect()
        return b + 1

    def E(ngn, d):
        f = ngn.F(d)
        ngn.collect()
        return d + f + 1

    def F(ngn, d):
        ngn.collect()
        return d + 1

    ngn = Engine.create([A, B, C, D, E, F], allow_edge_dependencies=True)
    assert ngn.A() == 8


def test_dep_proxy_nested_type_mixed():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        d = ngn.D(c)
        e = ngn.E(d)
        ngn.collect()
        return e

    def B(ngn):
        ngn.collect()
        return 1

    def C(ngn, b):
        ngn.collect()
        return b

    def D(ngn, c):
        ngn.collect()
        return c

    def E(ngn, d):
        f = ngn.F(d)
        g = ngn.G(f)
        ngn.collect()
        return g

    def F(ngn, d):
        ngn.collect()
        return d

    def G(ngn, f):
        ngn.collect()
        return f

    from darl import Engine

    ngn = Engine.create([A, B, C, D, E, F, G], allow_edge_dependencies=True)
    assert ngn.A() == 1


def test_callkey_nested_type_1():
    def A(ngn):
        b = ngn.callkey.B()
        c = ngn.callkey.C(b)
        d = ngn.callkey.D(c)

        b = ngn.run_call(b)
        c = ngn.run_call(c)
        d = ngn.run_call(d)
        ngn.collect()
        return b + c + d

    def B():
        return 1

    def C(ngn, b):
        b = ngn.run_call(b)
        ngn.collect()
        return b + 1

    def D(ngn, c):
        c = ngn.run_call(c)
        ngn.collect()
        return c + 1

    ngn = Engine.create([A, B, C, D])
    assert ngn.A() == 6


def test_callkey_nested_type_2():
    def A(ngn):
        b = ngn.callkey.B()
        c = ngn.C(b)
        ngn.collect()
        return c + 1

    def B():
        return 1

    def C(ngn, b):
        d = ngn.callkey.D(b)
        e = ngn.E(d)
        ngn.collect()
        return e + 1

    def D(ngn, b):
        match b:
            case CallKey():
                b = ngn.run_call(b)
        ngn.collect()
        return b + 1

    def E(ngn, d):
        match d:
            case CallKey():
                d = ngn.run_call(d)
        ngn.collect()
        return d + 1

    ngn = Engine.create([A, B, C, D, E])
    assert ngn.A() == 5


def test_dep_proxy_nested_type_1_scoped():
    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        d = ngn.D(c)
        ngn.collect()
        return b + c + d

    def B():
        return 1

    def C(ngn, b):
        d = ngn.D(b)
        ngn.collect()
        return b + d + 1

    def D(ngn, c):
        ngn.collect()
        return c + 1

    def D2(ngn, c):
        ngn.collect()
        return c + 2

    ngn = Engine.create([A, B, C, D], allow_edge_dependencies=True)
    assert ngn.A() == 10
    ngn = ngn.update({'D': D2})
    assert ngn.A() == 13

    ngn = Engine.create([A, B, C, D], allow_edge_dependencies=True)
    ngn = ngn.update({'D': D2}, scope=('C',))
    assert ngn.A() == 12


@pytest.mark.xfail
def test_dep_proxy_nested_type_2_scoped():
    raise NotImplementedError


def test_modified_dep_proxy_arg_should_fail_type_1():
    from darl.dep_proxy import DepProxyException

    def A(ngn):
        b = ngn.B()
        b = b + 1
        c = ngn.C(b)
        ngn.collect()
        raise NotImplementedError

    def B():
        return 1

    def C(ngn, b):
        ngn.collect()
        raise NotImplementedError

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    try:
        ngn.A()
    except DepProxyException:
        assert True
    else:
        assert False


def test_modified_dep_proxy_arg_should_fail_type_2():
    from darl.dep_proxy import DepProxyException

    def A(ngn):
        b = ngn.B()
        c = ngn.C(b)
        ngn.collect()
        raise NotImplementedError

    def B():
        return 1

    def C(ngn, b):
        # b here will be passed in as a depproxy, and can't be modified and passed into another service call
        b = b + 1
        c = ngn.C(b)
        ngn.collect()
        raise NotImplementedError

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    try:
        ngn.A()
    except DepProxyException:
        assert True
    else:
        assert False


def test_trace_1():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D(E):
        return E + 1

    def E(F):
        return F + 1

    def F():
        return 1

    ngn = Engine.create([A, B, C, D, E, F])
    try:
        ngn.trace()
    except ValueError:
        assert True
    else:
        assert False

    assert ngn.A() == 6
    trace = ngn.trace()
    ngn = ngn.shock('C', lambda x: x + 1)
    assert ngn.A() == 7
    trace2 = ngn.trace()

    assert trace.ups[0].ups[0].ups[0].ups[0].ups[0].ups == []
    assert trace2.ups[0].ups[0].ups[0].ups == []
    assert trace.ups[0].ups[0].downs[0].downs[0].node_id == trace.node_id

    build_id1 = ngn.executed_graph_build_ids[0]
    build_id2 = ngn.executed_graph_build_ids[1]
    assert build_id1 != build_id2

    c_trace = trace.ups[0].ups[0]
    c_trace2 = trace2.ups[0].ups[0].ups[0]

    assert c_trace.node_id == c_trace2.node_id
    assert c_trace.graph.graph_build_id == build_id1
    assert c_trace2.graph.graph_build_id == build_id2

    assert c_trace.computed
    assert c_trace2.from_cache

    c_computed_trace2 = c_trace2.computed_node
    assert c_computed_trace2.node_id == c_trace.node_id
    assert c_computed_trace2.graph.graph_build_id == c_trace.graph.graph_build_id

    assert c_trace.result == 4
    assert c_trace2.result == 4
    assert c_computed_trace2.result == 4


def test_trace_2():
    def A(B, C):
        return B + C

    def B(D):
        return D + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.A()
    d_thru_b = ngn.trace().ups[0].ups[0]
    d_thru_c = ngn.trace().ups[1].ups[0]

    assert d_thru_b.node_id == d_thru_c.node_id


def test_trace_status_with_error():
    from darl.constants import ExecutionStatus

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
        return c / d

    def C(ngn):
        return 1

    def D(ngn):
        return 0

    ngn = Engine.create([A, B, B2, C, D])
    ngn.D()
    try:
        ngn.A()
    except:
        assert True
    else:
        assert False

    trace = ngn.trace()
    assert trace.execution_status == ExecutionStatus.NOT_RUN
    assert trace.ups[0].execution_status == ExecutionStatus.ERRORED
    assert trace.ups[0].ups[0].execution_status == ExecutionStatus.CAUGHT_ERROR
    statuses = {}
    for i in [0, 1]:
        trace_i = trace.ups[0].ups[0].ups[i]
        service_name = trace_i.node_data['call_keys'][0].service_name
        statuses[service_name] = trace_i.execution_status
    assert statuses['C'] == ExecutionStatus.COMPUTED
    assert statuses['D'] == ExecutionStatus.FROM_CACHE


def test_trace_result_entry_removed():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C():
        return 1

    ngn = Engine.create([A, B, C])
    ngn.A()
    b_node = ngn.trace().ups[0].node_id
    del ngn.cache._cache[f'res:{b_node}']
    del ngn.cache._cache[f'res_meta:{b_node}']

    b_trace = ngn.trace().ups[0]
    try:
        b_trace.result
    except KeyError:
        assert True
    else:
        assert False
    assert b_trace.ups[0].node_data['call_keys'][0].service_name == 'C'  # assert still traversable


def test_trace_result_entry_removed_precache_run():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C():
        return 1

    ngn = Engine.create([A, B, C])
    ngn.A()
    trace1 = ngn.trace()
    ngn = ngn.shock('B', lambda x: x + 1)
    ngn.A()
    trace2 = ngn.trace()
    b_trace2 = trace2.ups[0].ups[0]
    b_node = b_trace2.node_id
    del ngn.cache._cache[f'res:{b_node}']
    del ngn.cache._cache[f'res_meta:{b_node}']

    # can traverse through computed nodes even though the result/meta was removed
    assert trace2.ups[0].ups[0].computed_node.ups[0].node_data['call_keys'][0].service_name == 'C'


def test_trace_full_graph():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C():
        return 1

    ngn = Engine.create([A, B, C])
    ngn.A()
    trace1 = ngn.trace()
    ngn = ngn.shock('B', lambda x: x + 1)
    ngn.A()
    trace2 = ngn.trace()

    assert trace2.ups[0].ups[0].ups == []
    assert trace2.ups[0].ups[0].full_graph().ups[0].ups == []

    try:
        trace2.ups[0].ups[0].full_graph().ups[0].result
    except ValueError:
        assert True
    else:
        assert False


@pytest.mark.xfail
def test_trace_inline():
    def A(B, C):
        return B + C

    def B(ngn):
        d = ngn.inline.D()
        ngn.collect()
        return d + 1

    def C(ngn):
        d = ngn.inline.D()
        ngn.collect()
        return d + 1

    def D(E):
        return E + 1

    def E(F):
        return F + 1

    def F():
        return 1

    ngn = Engine.create([A, B, C, D, E, F])
    assert ngn.A() == 8
    trace = ngn.trace()
    # TODO: executed graph being traversed is currently stunted by inline, fix that
    assert trace.ups[0].ups[0].ups[0].ups[0].ups == []  # make sure that executed graph traversed is not stunted by inline


def test_ngn_copy_trace():
    def A(B):
        return B

    def B():
        return 1

    ngn = Engine.create([A, B])
    ngn.A()
    first_item = ngn.executed_graph_build_ids[0]
    ngn1 = ngn.clone()
    ngn2 = ngn.clone()

    assert ngn.executed_graph_build_ids == [first_item]
    assert ngn1.executed_graph_build_ids == [first_item]
    assert ngn2.executed_graph_build_ids == [first_item]

    ngn1.A()
    assert ngn.executed_graph_build_ids == [first_item]
    assert len(ngn1.executed_graph_build_ids) == 2
    assert ngn1.executed_graph_build_ids[0] == first_item
    assert ngn2.executed_graph_build_ids == [first_item]

    ngn2.A()
    assert ngn.executed_graph_build_ids == [first_item]
    assert len(ngn1.executed_graph_build_ids) == 2
    assert len(ngn2.executed_graph_build_ids) == 2
    assert ngn2.executed_graph_build_ids[0] == first_item
    assert ngn1.executed_graph_build_ids != ngn2.executed_graph_build_ids


def test_ngn_copy_update():
    def A(B, C):
        return (B, C)

    class B:
        def __init__(self, ret_val=1):
            self.ret_val = ret_val

        def __call__(self):
            return self.ret_val

    class C:
        def __init__(self, ret_val=1):
            self.ret_val = ret_val

        def __call__(self):
            return self.ret_val

    ngn = Engine.create([A, B(), C()])
    ngn = ngn.update({'C': C(2)})
    ngn1 = ngn.copy()
    ngn2 = ngn.copy()
    ngn1 = ngn1.update({'B': B(2)})
    ngn2 = ngn2.update({'C': C(3)})

    assert ngn.A() == (1, 2)
    assert ngn1.A() == (2, 2)
    assert ngn2.A() == (1, 3)


def test_cycles_prohibited():
    from darl.graph import GraphCycleError

    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(A):
        return A + 1

    ngn = Engine.create([A, B, C])
    try:
        ngn.A()
    except GraphCycleError as e:
        assert "['A', 'B', 'C', 'A']" in e.args[0]
    else:
        assert False


def test_cycles_prohibited_callkey():
    from darl.graph import GraphCycleError

    def A(B):
        return B + 1

    def B(ngn):
        c = ngn.callkey.C()
        d = ngn.D(c)
        ngn.collect()
        return d + 1

    def C(A):
        return A + 1

    def D(ngn, x):
        x = ngn.run_call(x)
        ngn.collect()
        return x + 1

    ngn = Engine.create([A, B, C, D])
    try:
        ngn.A()
    except GraphCycleError as e:
        # TODO: should we expect this to be ABDCA? currently not because of call path overriding in callkeys
        assert "['A', 'B', 'C', 'A']" in e.args[0]
    else:
        assert False


def test_cache_consistent_across_scope_update():
    def A(ngn):
        b = ngn.B1()
        ngn.collect()
        return b

    def B1(B):
        return B

    def B(C, D):
        return C - D

    def C():
        return 3

    def D(E):
        return E + 1

    class E:
        def __init__(self, adder=0):
            self.adder = adder

        def __call__(self):
            return 1 + self.adder

    ngn = Engine.create([A, B1, B, C, D, E()])
    ngn1 = ngn.update({'E': E(1)}, scope=('B1',))
    ngn1.A()

    ngn2 = ngn.update({'E': E(1)})
    ngn2.B()

    ngn2_computed_nodes = [d for d in ngn2.trace().graph.nodes.values() if not d['from_cache_only']]
    assert len(ngn2_computed_nodes) == 0


def test_cache_consistent_across_scope_shock():
    def A(ngn):
        b = ngn.B1()
        ngn.collect()
        return b

    def B1(B):
        return B

    def B(C, D):
        return C - D

    def C():
        return 3

    def D(E):
        return E + 1

    class E:
        def __init__(self, adder=0):
            self.adder = adder

        def __call__(self):
            return 1 + self.adder

    ngn = Engine.create([A, B1, B, C, D, E()])
    ngn1 = ngn.shock(
        'E', lambda x: x + 1,
        scope=('B1',)
    )
    ngn1.A()

    ngn2 = ngn.shock(
        'E', lambda x: x + 1,
    )
    ngn2.B()

    ngn2_computed_nodes = [d for d in ngn2.trace().graph.nodes.values() if not d['from_cache_only']]
    assert len(ngn2_computed_nodes) == 0


def test_cache_1():
    from darl.cache import DictCache

    def A(B, C):
        return B + C

    def B():
        return 1

    class C:
        def __init__(self, x=1):
            self.x = x

        def __call__(self):
            return self.x

    cache = DictCache()
    ngn = Engine.create([A, B, C()], cache=cache)
    ngn.A()

    ngn = Engine.create([A, B, C(2)], cache=cache)
    ngn.A()

    assert ngn.trace().computed
    assert sorted(ngn.trace().ups, key=str)[0].from_cache
    assert sorted(ngn.trace().ups, key=str)[1].computed


def test_cache_purge():
    def A(B, C):
        return B + C

    def B():
        return 1

    def C():
        return 2

    ngn = Engine.create([A, B, C])
    ngn.A()
    ngn.A()
    assert ngn.trace().from_cache
    ngn.cache.purge()
    ngn.A()
    assert ngn.trace().computed


@pytest.mark.xfail
def test_cache_catch_error():
    # this test won't work as expected, the caching of ErrorSentinel's as regular values, throws it off
    # TODO: if we treated ErrorSentinels as no_cache this would work as expected
    #       e.g. after last downstream has used result, throw it out (but keep result metadata)

    def A(ngn):
        b = ngn.catch.B()
        ngn.collect()
        match b:
            case ngn.error(RuntimeError()):
                b = 99
        return b + 1

    def A2(B):
        return B + 1

    def B(C):
        return C + 1

    def C():
        raise RuntimeError()

    ngn = Engine.create([A, A2, B, C])
    ngn.A()  # from this call ErrorSentinels will be cached for B and C

    assert_provider_exception_raise(ngn.A2, RuntimeError)
    assert_provider_exception_raise(ngn.C, RuntimeError)  # this is a cached error sentinel, currently returns the error sentinel


@pytest.mark.xfail
def test_trace_catch_error():
    raise NotImplementedError()


def test_catch_error_downstream_value_hashed():
    def A(B):
        return B + 1

    def B(ngn):
        c = ngn.catch.C()
        ngn.collect()
        match c:
            case ngn.error(RuntimeError()):
                c = 99
        return c + 1

    def C(ngn):
        token = ngn.Token()
        ngn.collect()
        if token < 0:
            raise RuntimeError()
        else:
            return token

    values = iter([-1, 1])

    def Token():
        return next(values)

    ngn = Engine.create([A, B, C, Token])
    assert ngn.A() == 101
    assert ngn.A() == 101
    assert ngn.copy().A() == 101

    ngn = Engine.create([A, B, C, Token])
    assert ngn.A() == 3


def test_error_replay():
    from darl.trace import Trace

    counter = 0

    def A(B):
        return B + 1

    def B(ngn):
        c = ngn.C()
        d = ngn.D()
        ngn.collect()
        nonlocal counter
        counter += 1
        return c / d

    def C():
        nonlocal counter
        counter += 1
        return 1

    def D():
        nonlocal counter
        counter += 1
        return 0

    ngn = Engine.create([A, B, C, D])
    assert_provider_exception_raise(ngn.A, ZeroDivisionError)
    assert counter == 3

    failed_trace = ngn.trace().ups[0]
    assert_provider_exception_raise(failed_trace.replay, ZeroDivisionError)
    assert counter == 4  # should not have rerun any of the deps, only failed node

    trace = Trace.from_graph_build_id(ngn.executed_graph_build_ids[0], ngn.cache, failed_trace.node_id)
    assert_provider_exception_raise(trace.replay, ZeroDivisionError)
    assert counter == 5


def test_error_replay_precached():
    from darl.trace import Trace

    counter = 0

    def A(B):
        return B + 1

    def B(ngn):
        c = ngn.C()
        d = ngn.D()
        ngn.collect()
        nonlocal counter
        counter += 1
        return c / d

    def C():
        nonlocal counter
        counter += 1
        return 1

    def D():
        nonlocal counter
        counter += 1
        return 0

    ngn = Engine.create([A, B, C, D])
    ngn.C()
    ngn.D()
    assert_provider_exception_raise(ngn.A, ZeroDivisionError)
    assert counter == 3

    failed_trace = ngn.trace().ups[0]
    assert_provider_exception_raise(failed_trace.replay, ZeroDivisionError)
    assert counter == 4  # should not have rerun any of the deps, only failed node


def test_dont_compute_islands():
    def A(B):
        return B

    def B(C):
        return C

    def C(D):
        return D

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.C()
    del ngn.cache._cache[f'res:{ngn.trace().ups[0].node_id}']
    del ngn.cache._cache[f'res_meta:{ngn.trace().ups[0].node_id}']
    ngn.A()
    exec_graph = ngn.cache.get(f'executed_graph:{ngn.executed_graph_build_ids[-1]}').graph
    full_graph = ngn.cache.get(f'full_graph:{ngn.executed_graph_build_ids[-1]}').graph

    assert exec_graph.root_nodes() == full_graph.root_nodes()


def test_replay_successful_node():
    counter = 0

    def A(B):
        nonlocal counter
        counter += 1
        return B

    def B():
        return 1

    ngn = Engine.create([A, B])
    ngn.A()
    ngn.trace().replay()
    assert counter == 2


# should we fail this one if direct deps are not cached? currently we don't
# potentially un-xfail this
@pytest.mark.xfail
def test_replay_direct_upstream_node_removed():  # test replaying with computing deeper than one level
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.A()
    original_b_trace = ngn.trace().ups[0]

    del ngn.cache._cache[f'res:{original_b_trace.ups[0].node_id}']
    del ngn.cache._cache[f'res_meta:{original_b_trace.ups[0].node_id}']
    try:
        original_b_trace.replay()
    except:
        assert True  # doesn't actually get hit
    else:
        assert False  # currently can run deeper than one level if upstream deps are not in cache, do we allow that?


def test_replay_dont_populate_cache():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.A()
    original_b_trace = ngn.trace().ups[0]

    del ngn.cache._cache[f'res:{original_b_trace.node_id}']
    del ngn.cache._cache[f'res_meta:{original_b_trace.node_id}']

    original_b_trace.replay()

    assert f'res:{original_b_trace.node_id}' not in ngn.cache._cache


def test_replay_fidelity_to_original_run():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.A()
    original_b_trace = ngn.trace().ups[0]

    del ngn.cache._cache[f'res:{original_b_trace.ups[0].node_id}']
    del ngn.cache._cache[f'res_meta:{original_b_trace.ups[0].node_id}']

    ngn.C()
    try:
        original_b_trace.replay()  # should fail since the C node is from a different run and could have a different result
    except AssertionError:
        assert True
    else:
        assert False


def test_replay_fidelity_to_original_precached_run():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.C()
    ngn.A()
    original_b_trace = ngn.trace().ups[0]

    del ngn.cache._cache[f'res:{original_b_trace.ups[0].node_id}']
    # del ngn.cache._cache[f'res_meta:{original_b_trace.ups[0].node_id}']

    try:
        original_b_trace.replay()
    except KeyError:
        assert True  # will try to run and pull from cache even though result is not there
    else:
        assert False


def test_replay_fidelity_to_original_precached_run_repopulated():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.C()
    ngn.A()
    original_b_trace = ngn.trace().ups[0]

    del ngn.cache._cache[f'res:{original_b_trace.ups[0].node_id}']
    del ngn.cache._cache[f'res_meta:{original_b_trace.ups[0].node_id}']

    ngn.C()  # repopulating
    try:
        # should fail since even though in both original A/B run and this replay the result is pulled from cache,
        # in the replay the cache is populated from a different run
        original_b_trace.replay()
    except AssertionError:
        assert True
    else:
        assert False


def test_run_upstream_cached_node_removed_root_still_cached():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.C()
    ngn.A()
    original_b_trace = ngn.trace().ups[0]

    del ngn.cache._cache[f'res:{original_b_trace.ups[0].node_id}']
    del ngn.cache._cache[f'res_meta:{original_b_trace.ups[0].node_id}']

    assert ngn.A() == 4  # was previously failing due to trim_graph bug
    assert ngn.trace().ups == []


def test_run_upstream_cached_node_removed_root_not_cached():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C(D):
        return D + 1

    def D():
        return 1

    ngn = Engine.create([A, B, C, D])
    ngn.C()
    ngn.A()
    original_b_trace = ngn.trace().ups[0]

    del ngn.cache._cache[f'res:{original_b_trace.ups[0].node_id}']
    del ngn.cache._cache[f'res_meta:{original_b_trace.ups[0].node_id}']

    del ngn.cache._cache[f'res:{original_b_trace.downs[0].node_id}']
    del ngn.cache._cache[f'res_meta:{original_b_trace.downs[0].node_id}']

    assert ngn.A() == 4
    assert ngn.trace().ups[0].ups == []


def test_trace_result_cache_repopulated():
    def A(B):
        return B + 1

    def B():
        return 1

    ngn = Engine.create([A, B])
    ngn.A()

    trace = ngn.trace()
    del ngn.cache._cache[f'res:{trace.ups[0].node_id}']
    ngn.B()
    try:
        trace.ups[0].result
    except ValueError:
        assert True
    else:
        assert False


def test_trace_result_precache_boundary_repopulated():
    def A(B):
        return B + 1

    def B(C):
        return C + 1

    def C():
        return 1

    ngn = Engine.create([A, B, C])
    ngn.B()
    ngn.A()

    trace = ngn.trace()
    try:
        trace.full_graph().ups[0].ups[0].result
    except ValueError:
        assert True
    else:
        assert False


def test_iter():
    def A(ngn):
        bcs = []
        for b in ngn.iter(n=5).Bs():
            bc = ngn.C(b)
            bcs.append(bc)
        ngn.collect()
        return bcs

    def Bs():
        return ['b1', 'b2', 'b3']

    def C(ngn, b):
        xb = ngn.X(b)
        ngn.collect()
        return 'c' + xb

    def X(ngn, b):
        ngn.collect()
        return 'x' + b

    ngn = Engine.create([A, Bs, C, X], allow_edge_dependencies=True)
    assert ngn.A() == ['cxb1', 'cxb2', 'cxb3']


def test_iter_under_estimate():
    def A(ngn):
        bs = []
        for b in ngn.iter(n=2).Bs():
            bs.append(b)
        ngn.collect()
        return bs

    def Bs():
        return ['b1', 'b2', 'b3']

    ngn = Engine.create([A, Bs], allow_edge_dependencies=True)
    assert_provider_exception_raise(ngn.A, ValueError)


def test_iter_non_index_slice_failure():
    def A(ngn):
        bcs = []
        for b in ngn.iter(n=5).Bs():
            bc = ngn.C(b)
            bcs.append(bc)
        ngn.collect()
        return bcs

    def Bs():
        return ['b1', 'b2', 'b3']

    def C(ngn, b):
        xb = ngn.X(b)
        ngn.collect()
        return 'c' + xb

    def X(ngn, b):
        ngn.collect()
        raise RuntimeError()

    ngn = Engine.create([A, Bs, C, X], allow_edge_dependencies=True)
    assert_provider_exception_raise(ngn.A, RuntimeError)


# def test_iter_nested():
#     def TotalGDP(ngn):
#         results = []
#         for country in ngn.iter(n=5).Countries():
#         # for country in ngn.inline.Countries():
#             results.append(ngn.GDP(country))
#         ngn.collect()
#         return sum(results)
#
#     def Countries(ngn):
#         counted_countries = ngn.CountedCountries()
#         ngn.collect()
#         return counted_countries + ['C']
#
#     def CountedCountries(ngn):  # nested in the iter called service
#         country_pops = {}
#         for country in ngn.iter(n=7).AllCountries():
#         # for country in ngn.inline.AllCountries():
#             country_pops[country] = ngn.CountryPopulation(country, 0)
#         ngn.collect()
#         return [c for c, pop in country_pops.items() if pop > 4]
#
#     def AllCountries():
#         return ['A', 'B', 'X', 'Y']
#
#     def CountryPopulation(ngn, country, dummy):  # dumy just to ensure no node merging
#         pops = []
#         for state in ngn.iter(n=5).States(country):
#         # for state in ngn.inline.States(country):
#             pops.append(ngn.StatePopulation(state))
#         ngn.collect()
#         return sum(pops)
#
#     def StatePopulation(ngn, state):
#         ngn.collect()
#         return {
#             'A_1': 3,
#             'A_2': 2,
#             'B_1': 2,
#             'B_2': 4,
#             'B_3': 1,
#             'C_1': 0,
#             'X_1': 3,
#             'Y_1': 2,
#         }[state]
#
#     def States(ngn, country):
#         ngn.collect()
#         return {
#             'A': ['A_1', 'A_2'],
#             'B': ['B_1', 'B_2', 'B_3'],
#             'C': ['C_1'],
#             'X': ['X_1'],
#             'Y': ['Y_1'],
#         }[country]
#
#     def GDP(ngn, country):
#         pop = ngn.CountryPopulation(country, 1)
#         ngn.collect()
#         return pop * 2
#
#     ngn = Engine.create([TotalGDP, Countries, CountedCountries, AllCountries, CountryPopulation, StatePopulation, States, GDP])
#     ngn.TotalGDP()


# TODO: test with differing length nested iters
def test_iter_nested_type1():
    # nested iter call happens in the service that is being itered
    def A(ngn):
        cs = []
        for b in ngn.iter(n=5).Bs():
            cs.append(ngn.C(b))
        ngn.collect()
        return ['a' + c for c in cs]

    def Bs(BsInner):  # have this dummy layer to have some separation between the iter'ing providers
        return BsInner

    def BsInner(ngn):
        ys = []
        for x in ngn.iter(n=5).Xs():
            ys.append(ngn.Y(x))
        ngn.collect()
        return ['b' + y for y in ys]

    def C(ngn, b):
        ngn.collect()
        return 'c' + b

    def Xs(XsInner):
        return XsInner

    def XsInner():
        return ['x1', 'x2', 'x3']

    def Y(ngn, x):
        ngn.collect()
        return 'y' + x

    ngn = Engine.create([A, Bs, BsInner, C, Xs, XsInner, Y], allow_edge_dependencies=True)
    ngn.A()
    assert ngn.A() == ['acbyx1', 'acbyx2', 'acbyx3']


# TODO: test with differing length nested iters
@pytest.mark.parametrize('bs, xs, expected_result', [
    (['b1', 'b2', 'b3'], ['x1', 'x2', 'x3'], ['acb1yx1cb1yx2cb1yx3', 'acb2yx1cb2yx2cb2yx3', 'acb3yx1cb3yx2cb3yx3']),
    (['b1', 'b2', 'b3'], ['x1', 'x2'], ['acb1yx1cb1yx2', 'acb2yx1cb2yx2', 'acb3yx1cb3yx2']),
    (['b1', 'b2'], ['x1', 'x2', 'x3'], ['acb1yx1cb1yx2cb1yx3', 'acb2yx1cb2yx2cb2yx3']),
])
def test_iter_nested_type2(bs, xs, expected_result):
    # nested iter call happens in the service that an iter item is being passed to
    def A(ngn):
        cs = []
        for b in ngn.iter(n=5).Bs():
            cs.append(ngn.C(b))
        ngn.collect()
        return ['a' + c for c in cs]

    def Bs():
        # return ['b1', 'b2', 'b3']
        return bs

    def C(ngn, b):  # have this dummy layer to have some separation between the iter'ing providers
        c = ngn.CInner(b)
        ngn.collect()
        return c

    def CInner(ngn, b):
        ys = []
        for x in ngn.iter(n=5).Xs():
            ys.append(ngn.Y(x))
        ngn.collect()
        return ''.join(['c' + b + y for y in ys])

    def Xs():
        # return ['x1', 'x2', 'x3']
        return xs

    def Y(ngn, x):
        ngn.collect()
        return 'y' + x

    ngn = Engine.create([A, Bs, C, CInner, Xs, Y], allow_edge_dependencies=True)
    # assert ngn.A() == ['acb1yx1cb1yx2cb1yx3', 'acb2yx1cb2yx2cb2yx3', 'acb3yx1cb3yx2cb3yx3']
    assert ngn.A() == expected_result


def test_iter_nested_type3():
    # nested iter call happens in the service that an iter item is being passed to
    def A(ngn):
        cs = []
        for b in ngn.iter(n=5).Bs():
            cs.append(ngn.C(b))
        ngn.collect()
        return ['a' + c for c in cs]

    def Bs():
        return ['b1', 'b2', 'b3']

    def C(ngn, b):  # have this dummy layer to have some separation between the iter'ing providers
        c = ngn.CInner(b)
        ngn.collect()
        return c

    def CInner(ngn, b):
        ys = []
        for x in ngn.iter(n=5).Xs(b):  # pass iter item here
            ys.append(ngn.Y(x, b))  # and pass iter item here too
        ngn.collect()
        return ''.join(['c' + b + y for y in ys])

    def Xs(ngn, b):
        ngn.collect()
        if b == 'b1':
            return ['x1', 'x2', 'x3']
        elif b == 'b2':
            return ['x1', 'x2']
        else:
            return ['x1', 'x2', 'x3', 'x4']

    def Y(ngn, x, b):
        ngn.collect()
        return 'y' + b + x

    ngn = Engine.create([A, Bs, C, CInner, Xs, Y], allow_edge_dependencies=True)
    assert ngn.A() == ['acb1yb1x1cb1yb1x2cb1yb1x3', 'acb2yb2x1cb2yb2x2', 'acb3yb3x1cb3yb3x2cb3yb3x3cb3yb3x4']


@pytest.mark.parametrize('n', [3, 4])
def test_iter_top_level_ngn(n):
    def Xs():
        return [1, 2, 3]

    ngn = Engine.create([Xs])
    assert ngn.iter(n=n).Xs() == [1, 2, 3]


@pytest.mark.xfail()
def test_iter_top_level_ngn_underestimate():
    def Xs():
        return [1, 2, 3]

    ngn = Engine.create([Xs])
    try:
        ngn.iter(n=2).Xs()
    except ValueError:
        assert True
    else:
        assert False


def test_iter_unpacking():
    def A(ngn):
        x1, x2 = ngn.iter(n=2).Xs()
        b1 = ngn.B(x1)
        b2 = ngn.B(x2)
        ngn.collect()
        return (x1, x2, b1, b2)

    def B(ngn, x):
        ngn.collect()
        return 'b' + x

    def Xs():
        return ['x1', 'x2']

    ngn = Engine.create([A, B, Xs], allow_edge_dependencies=True)
    assert ngn.A() == ('x1', 'x2', 'bx1', 'bx2')


def test_modify_result_in_place():
    # test if you modify a cached result will it have an effect on that result elsewhere?
    def A(B, C):
        return B + C

    def B(D):
        D.append(3)
        return D

    def C(D):
        return D

    def D():
        return [1, 2]

    ngn = Engine.create([A, B, C, D])
    assert ngn.A() == [1, 2, 3, 1, 2]


def test_exception_trace():
    def Root(A):
        return A + 1

    def A(ngn):
        b = ngn.B()
        ngn.collect()
        return b + 1

    def B():
        raise RuntimeError()

    ngn = Engine.create([Root, A, B])
    try:
        ngn.Root()
    except ProviderException as e:
        graph_build_id = e.args[0].split('\n')[2].split(': ')[1]
        node_id = e.args[0].split('\n')[3].split(': ')[1]
        trace = Trace.from_graph_build_id(graph_build_id, ngn.cache, node_id)
        assert trace.node_data['call_keys'][0].service_name == 'B'
    else:
        assert False


def test_trace_value_hashed():
    # make sure value hashed execution doesn't add root invocations to main ngn
    def A(B):
        return B + 1

    @value_hashed
    def B():
        return 1

    ngn = Engine.create([A, B])
    ngn.A()
    assert len(ngn.executed_graph_build_ids) == 1


def test_anonymous_service():
    def A(ngn):
        b = ngn.B()
        c = ngn[C]()
        ngn.collect()
        return b + c

    def B(ngn):
        c = ngn[C]()
        ngn.collect()
        return c + 1

    def C(ngn):
        d = ngn.D()
        ngn.collect()
        return d + 1

    def D():
        return 1

    ngn = Engine.create([A, B, D], allow_anon_services=True)
    assert ngn.A() == 5


def test_anonymous_service_not_allowed():
    def A(ngn):
        b = ngn.B()
        c = ngn[C]()
        ngn.collect()
        return b + c

    def B(ngn):
        c = ngn[C]()
        ngn.collect()
        return c + 1

    def C(ngn):
        d = ngn.D()
        ngn.collect()
        return d + 1

    def D():
        return 1

    ngn = Engine.create([A, B, D], allow_anon_services=False)
    try:
        assert ngn.A() == 5
    except ValueError:
        assert True
    else:
        assert False


def test_anon_service_dep_proxy():
    def A(ngn):
        b = ngn.B()
        c = ngn[C](b)
        d = ngn.D(c)
        ngn.collect()
        return d

    def B():
        return 1

    def C(ngn, b):
        ngn.collect()
        return b + 1

    def D(ngn, c):
        ngn.collect()
        return c + 1

    ngn = Engine.create([A, B, D], allow_edge_dependencies=True, allow_anon_services=True)
    assert ngn.A() == 3


def test_anon_service_lambda_function_dep_proxy():  # no point in ever really using a lambda without dep proxy
    def A(ngn):
        b = ngn.B()
        c = ngn[lambda ngn, b: b + 1](b)
        d = ngn.D(c)
        ngn.collect()
        return d

    def B():
        return 1

    def D(ngn, c):
        ngn.collect()
        return c + 1

    ngn = Engine.create([A, B, D], allow_edge_dependencies=True, allow_anon_services=True)
    assert ngn.A() == 3


def test_anon_service_lambda_function_dep_proxy_multi():
    def A(ngn):
        b = ngn.B()
        c = ngn[lambda ngn, b: b + 1](b)
        d = ngn.D(c)
        e = ngn[lambda ngn, d: d + 1](d)
        ngn.collect()
        return e

    def B():
        return 1

    def D(ngn, c):
        ngn.collect()
        return c + 1

    ngn = Engine.create([A, B, D], allow_edge_dependencies=True, allow_anon_services=True)
    assert ngn.A() == 4


def test_anon_service_only():  # includes top level engine anon service
    def Root(ngn):
        e = ngn[lambda ngn: 1]()
        d = ngn[lambda ngn: 1]()
        c = ngn[lambda ngn: 1]()
        b = ngn[lambda ngn, d, e: d + e](d, e)
        a = ngn[lambda ngn, b, c, e: b + c + e](b, c, e)
        ngn.collect()
        return a

    ngn = Engine.create([], allow_edge_dependencies=True, allow_anon_services=True)
    assert ngn[Root]() == 4


def test_anon_service_callkey():
    def Root(ngn):
        a = ngn.callkey[A]()
        b = ngn.callkey[lambda ngn, x: x + 1](1)
        c1 = ngn[C](a)
        c2 = ngn[C](b)  # complicated because lambda redefined each pass through and shows up in callkey kwargs by memory id
        ngn.collect()
        return c1 + c2

    def A(ngn):
        ngn.collect()
        return 1

    def C(ngn, b):
        b = ngn.run_call(b)
        ngn.collect()
        return b + 1

    ngn = Engine.create([], allow_anon_services=True)
    assert ngn[Root]() == 5


def test_ngn_provider_no_collect():
    def A(ngn):
        b1 = ngn.B(1)
        b2 = ngn.B(b1)
        ngn.collect()
        return b2 + 1

    def B(ngn, x):
        return x + 1

    ngn = Engine.create([A, B], allow_edge_dependencies=True)
    assert ngn.A() == 4


@pytest.mark.xfail
def test_ngn_provider_no_collect_call_made():
    def A(ngn):
        b = ngn.B()
        return b + 1

    def B(ngn):
        return 1

    ngn = Engine.create([A, B])
    try:
        ngn.A()
    except RuntimeError:  # placehlder exception need explicit exception here
        assert True
    else:
        assert False



# region these can't be nested functions to work with pickle...
class _ngn_serialization_A:
    def __init__(self, deps):
        self.deps = deps

    def __call__(self, ngn):
        res = []
        for dep in self.deps:
            res.append(ngn[dep]())
        ngn.collect()
        return sum(res)

def _ngn_serialization_B():
    return 1

def _ngn_serialization_C():
    return 2
# endregion

def test_ngn_serialization():
    import pickle
    ngn = Engine.create([
        _ngn_serialization_A(['_ngn_serialization_B', '_ngn_serialization_C']),
        _ngn_serialization_B,
        _ngn_serialization_C,
    ])
    assert pickle.loads(pickle.dumps(ngn))._ngn_serialization_A() == 3


def test_graph_caching():
    def A(B, C):
        return B + C

    def B(C):
        return C + 9

    def C():
        return 0.5

    ngn = Engine.create([A, B, C])
    try:
        # initial empty should fail
        G = ngn.compile(ngn.callkey.A(), from_cache_only=True)
    except ValueError:
        assert True
    else:
        assert False
    # caches graph
    G = ngn.compile(ngn.callkey.A())

    # work from same ngn
    G = ngn.compile(ngn.callkey.A(), from_cache_only=True)

    # work from new engine with same cache
    ngn = Engine.create([A, B, C], cache=ngn.cache)
    G = ngn.compile(ngn.callkey.A(), from_cache_only=True)

    # work with identical update
    ngn = ngn.update({'C': C})
    G = ngn.compile(ngn.callkey.A(), from_cache_only=True)


def test_graph_caching_miss():
    def A(B, C):
        return B + C

    def B(C):
        return C + 9

    def C1():
        return 0.5
    C1.service_name = 'C'

    def C2():
        return 0.6
    C2.service_name = 'C'

    ngn = Engine.create([A, B, C1])
    ngn.compile(ngn.callkey.A())
    ngn.compile(ngn.callkey.A(), from_cache_only=True)

    ngn = ngn.update({'C': C2})
    try:
        # mismatch in providers should cause no graph pull from cache
        G = ngn.compile(ngn.callkey.A(), from_cache_only=True)
    except ValueError:
        assert True
    else:
        assert False


def test_graph_cache_with_args():
    def A(ngn, x):
        b = ngn.B(x)
        c = ngn.C(x)
        ngn.collect()
        return b + c

    def B(ngn, x):
        c = ngn.C(x)
        ngn.collect()
        return c + 1

    def C(ngn, x):
        return x + 1

    ngn = Engine.create([A, B, C])
    G = ngn.compile(ngn.callkey.A(1))
    G = ngn.compile(ngn.callkey.A(1), from_cache_only=True)


def test_graph_cache_with_dep_proxy():
    def A(ngn, x):
        b = ngn.B(x)
        c = ngn.C(b)
        d = ngn.D(b)
        ngn.collect()
        return b + c + d

    def B(ngn, x):
        c = ngn.C(x)
        ngn.collect()
        return c + 1

    def C(ngn, x):
        return x + 1

    ngn = Engine.create([A, B, C], allow_edge_dependencies=True)
    ngn.update({'D': C}, inplace=True)
    G1 = ngn.compile(ngn.callkey.A(1))
    G2 = ngn.compile(ngn.callkey.A(1), from_cache_only=True)

    assert ngn.runner.run(G1, ngn.cache) == 11
    assert ngn.runner.run(G2, ngn.cache) == 11


# TODO: need many more graph caching tests for different edge cases


def test_graph_cache_subset_existing_graph():
    def A(B, C):
        return B + C

    def B(C):
        return C + 9

    def C():
        return 0.5

    ngn = Engine.create([A, B, C])
    ngn.compile(ngn.callkey.A())
    ngn.compile(ngn.callkey.B(), from_cache_only=True)


def test_graph_cache_with_value_hashed():
    t1_vals = iter([1, 2, 1])
    t2_vals = iter([3, 4, 3])

    def A(ngn):
        t1 = ngn.token1()
        t2 = ngn.token2(1)
        ngn.collect()
        return t1 + t2

    @value_hashed
    def token1():
        return next(t1_vals)

    @value_hashed
    def token2(ngn, x):
        ngn.collect()
        return next(t2_vals) + x

    cache = DictCache()

    ngn = Engine.create([A, token1, token2], cache=cache)
    ngn.compile(ngn.callkey.A())
    ngn.compile(ngn.callkey.A(), from_cache_only=True)

    ngn = Engine.create([A, token1, token2], cache=cache)
    try:
        # don't want to pull from cache here, because token should return a new value which means old graph no longer valid
        ngn.compile(ngn.callkey.A(), from_cache_only=True)
    except ValueError:
        assert True
    else:
        assert False
    ngn.compile(ngn.callkey.A())
    ngn.compile(ngn.callkey.A(), from_cache_only=True)

    ngn = Engine.create([A, token1, token2], cache=cache)
    # do want to pull from cache here, because token should return a new value, but was previously associated with a cached graph
    ngn.compile(ngn.callkey.A(), from_cache_only=True)

    try:
        next(t1_vals)
    except StopIteration:
        assert True
    else:
        assert False

    try:
        next(t2_vals)
    except StopIteration:
        assert True
    else:
        assert False


@pytest.mark.xfail  # disabled due to bug tested in `test_graph_cache_scoped_update_exists_but_not_calling_in_that_scope`
def test_graph_cache_with_scoped_update():
    def A(B, C):
        return B + C

    def B(C):
        return C

    def C():
        return 1

    def C2():
        return 2

    ngn = Engine.create([A, B, C])
    ngn = ngn.update({'C': C2}, scope=('B',))
    ngn.compile(ngn.callkey.A())
    ngn.compile(ngn.callkey.A(), from_cache_only=True)


@pytest.mark.xfail  # disabled due to bug tested in `test_graph_cache_scoped_update_exists_but_not_calling_in_that_scope`
def test_graph_cache_with_non_realized_scoped_update():
    def A(B, C):
        return B + C

    def B(C):
        return C

    def C():
        return 1

    def C2():
        return 2

    ngn = Engine.create([A, B, C])
    ngn = ngn.update({'C': C2}, scope=('B', 'X'))
    ngn.compile(ngn.callkey.A())
    ngn.compile(ngn.callkey.A(), from_cache_only=True)


def test_graph_cache_no_scoped_update_to_scoped_update():
    # previously had a bug that caused bad cache hits in cases like illustrated below

    def A(B, C):
        return B + C

    def B(D):
        return D + 1

    def C(D):
        return D + 2

    def D():
        return 1

    def D_prime():
        return 2

    ngn = Engine.create([A, B, C, D])
    ngn.compile(ngn.callkey.A())

    ngn = ngn.update({'D': D_prime}, scope=('B',))
    try:
        ngn.compile(ngn.callkey.A(), from_cache_only=True)
    except ValueError:
        assert True
    else:
        assert False


def test_graph_cache_scoped_update_exists_but_not_calling_in_that_scope():
    # this test currently only works because we are disabling graph caching when scoped updates are present
    def A(ngn, x, y):
        b = ngn.B(x, y)
        ngn.collect()
        return b + 1

    def B(ngn, x, y):
        return x + y

    def B_prime(ngn, x, y):
        return x - y

    ngn = Engine.create([A, B])
    ngn2 = ngn.update({'B': B_prime})
    ngn3 = ngn.update({'B': B_prime}, scope=('A',))

    assert ngn2.A(1, 2) == 0
    # below would only break if above was run first

    assert ngn3.A(1, 2) == 0
    # in the past this was pulling 0 from cache
    # since it was pulling the graph from the ngn3.A run above ( but only if ngn2 run happened first)
    assert ngn3.B(1, 2) == 3



def test_pin():
    # this test currently only works because we are disabling graph caching when scoped updates are present
    # same bug as in `test_graph_cache_scoped_update_exists_but_not_calling_in_that_scope`
    def A(ngn, x, y):
        b = ngn.B(x, y)
        ngn.collect()
        return b + 1

    def B(ngn, x, y):
        return x + y

    ngn = Engine.create([A, B])
    ngn2 = ngn.pin(ngn.callkey.B(1, 2), 999)
    ngn3 = ngn.pin(ngn.callkey.B(1, 2), 999, scope=('A',))

    assert ngn.A(1, 2) == 4
    assert ngn.B(1, 2) == 3

    assert ngn2.A(1, 2) == 1000
    assert ngn2.B(1, 2) == 999

    assert ngn3.A(1, 2) == 1000
    assert ngn3.B(1, 2) == 3

    ngn4 = ngn2.pin(ngn.callkey.B(1, 3), 777)

    assert ngn4.B(1, 2) == 999
    assert ngn4.B(1, 3) == 777
    assert_provider_exception_raise(lambda: ngn4.B(1, 9), KeyError)


def test_pin_non_existing_service():
    from darl.call_key import CallKey

    def A(ngn, x, y):
        b = ngn.B(x, y)
        ngn.collect()
        return b + 1

    ngn = Engine.create([A])
    ngn2 = ngn.pin(CallKey('B', {'x': 1, 'y': 2}), 999)

    assert ngn2.A(1, 2) == 1000
    assert ngn2.B(1, 2) == 999
