"""Byte-preserving diagnostic logs with an explicit hard capture bound."""
class LogLimitExceeded(RuntimeError):
    def __init__(self, limit, captured, observed):
        self.limit,self.captured,self.observed=limit,captured,observed
        super().__init__('diagnostic stderr limit reached: captured=%d observed=%d limit=%d' % (captured,observed,limit))

class BoundedLog:
    def __init__(self, output, limit):
        if type(limit) is not int or limit<1:raise ValueError('positive byte limit required')
        self.output,self.limit,self.captured,self.observed=output,limit,0,0
    def append(self, data):
        room=self.limit-self.captured
        self.observed+=len(data)
        captured=data[:room]
        self.output.write(captured);self.output.flush();self.captured+=len(captured)
        if len(data)>room:raise LogLimitExceeded(self.limit,self.captured,self.observed)
