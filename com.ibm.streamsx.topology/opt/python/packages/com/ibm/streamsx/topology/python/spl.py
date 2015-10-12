from enum import Enum
import functools
import inspect

OperatorType = Enum('OperatorType', 'Ignore Source Sink Function')
OperatorType.Function.spl_template = 'PythonTupleFunction'
OperatorType.Sink.spl_template = 'PythonTupleSink'

# Allows functions in any module in opt/python/streams to be explicitly ignored.
def operator(wrapped):
    @functools.wraps(wrapped)
    def _operator(*args, **kwargs):
        return wrapped(*args, **kwargs)
    _operator.__splpy_optype = OperatorType.Function
    _operator.__splpy_file = inspect.getsourcefile(wrapped)
    return _operator

def ignore(wrapped):
    @functools.wraps(wrapped)
    def _ignore(*args, **kwargs):
        return wrapped(*args, **kwargs)
    _ignore.__splpy_optype = OperatorType.Ignore
    _ignore.__splpy_file = inspect.getsourcefile(wrapped)
    return _ignore

# Defines a function as a sink operator
def sink(wrapped):
    @functools.wraps(wrapped)
    def _sink(*args, **kwargs):
        ret = wrapped(*args, **kwargs)
        assert ret == None, "SPL @sink function must not return any value, except None"
        return None
    _sink.__splpy_optype = OperatorType.Sink
    _sink.__splpy_file = inspect.getsourcefile(wrapped)
    return _sink
