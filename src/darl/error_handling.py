class UpstreamException(Exception):
    """
    To be raised when an error sentinel is not properly handled
    """
    pass


class ErrorSentinel:
    __match_args__ = ('error',)

    def __init__(self, error, origin_cache_key):
        self.error = error
        self.origin_cache_key = origin_cache_key


class IterIndexError(Exception):
    pass


class ProviderException(Exception):
    """
    Used to identify exceptions that arise within provider logic (as opposed to framework)
    """
    pass


def raise_provider_exception(e, graph_build_id, cache_key):
    # TODO: a lot of room for improvement in exception handling (separte from ngn.catch error handling)
    # TODO: need a way to describe how to recreate the cache for trace to work
    # call `import ipdb; ipdb.post_mortem(e.__traceback__)` to debug into the main underlying exception
    raise ProviderException(
        f'Error encountered in provider logic (see chained exception traceback above)\n'
        f'The above error occured at\n'
        f'graph_build_id: {graph_build_id}\n'
        f'cache_key: {cache_key}\n\n'
        '(If using local post mortem debugger, to step into main underlying exception run this: `import ipdb; ipdb.post_mortem(e.__traceback__)`  )'
    ) from e
    # exc_type, exc_value, exc_tb = sys.exc_info()
    # new_exc = type(e)(f"{e}\n\nThe above error occured at\ngraph_build_id: {graph_build_id}\nnode_id: {cache_key}")
    # raise new_exc.with_traceback(exc_tb)


# def test1():
#     test2()
#
# def test2():
#     test3()
#
# def test3():
#     raise ValueError('og exception')
#
# def testA():
#     try:
#         test1()
#     except ValueError as e:
#         raise RuntimeError() from e
#
# def testB():
#     testA()
#
# def testC():
#     try:
#         testB()
#     except RuntimeError as e:
#         og_exc = e.__cause__
#         new_exc = type(og_exc)(f"{og_exc}\n\nExtra context")
#         tb = og_exc.__traceback__
#         while tb.tb_next:
#             tb = tb.tb_next
#         raise new_exc.with_traceback(tb) from None
#
#
# testC()

