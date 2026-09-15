"""Inject a synthetic owner correction when a model proposes its first write."""


class CorrectionProvider:
    def __init__(self,provider):
        self.provider=provider
        self.triggered=False
        self.armed=True

    def call(self,*args,**kwargs):
        result=self.provider.call(*args,**kwargs)
        if self.armed and result.get('action')=='tool' and result.get('tool')=='calendar.change':
            self.triggered=True
        return result

    def update(self):
        return 'synthetic-owner-correction' if self.armed and self.triggered else None
