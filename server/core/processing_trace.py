"""Structured, job-scoped diagnostics shared by terminal and per-output trace."""
import json
import logging
import math
import re
import threading
import time
from pathlib import Path

_LOCAL=threading.local()
_LOG=logging.getLogger('klipzy.processing')
_SECRET=re.compile(r'(token|password|secret|authorization|api[_-]?key)',re.I)

def clean(value,key=''):
    if _SECRET.search(key):return '[REDACTED]'
    if isinstance(value,dict):return {str(k):clean(v,str(k)) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        result=[]
        for i,v in enumerate(value):
            previous=str(value[i-1]) if i else ''
            result.append('[REDACTED]' if previous.startswith('-') and _SECRET.search(previous) else clean(v))
        return result
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    if isinstance(value,str):
        value=re.sub(r'(?i)(bearer\s+)\S+',r'\1[REDACTED]',value)
        value=re.sub(r'(?i)([?&](?:token|api_key|key|access_token)=)[^&\s]+',r'\1[REDACTED]',value)
        value=re.sub(r'(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s&,;]+',r'\1[REDACTED]',value)
        value=re.sub(r'(https?://)[^/\s@]+@',r'\1[REDACTED]@',value)
        return value
    return value

def bind(job_id):
    _LOCAL.job_id=job_id;_LOCAL.path=None;_LOCAL.start=time.monotonic()

def attach(path):
    _LOCAL.path=Path(path)

def unbind():
    _LOCAL.__dict__.clear()

def active():return bool(getattr(_LOCAL,'job_id',None))

def event(name,**details):
    record=clean({'job_id':getattr(_LOCAL,'job_id',None),'event':name,
        'elapsed_seconds':round(time.monotonic()-getattr(_LOCAL,'start',time.monotonic()),3),**details})
    line=json.dumps(record,ensure_ascii=True,default=str,allow_nan=False)
    level = logging.ERROR if name in ('job.failed', 'pipeline.failed') else logging.WARNING if name.endswith('.failed') or name.endswith('.timeout') else logging.INFO
    _LOG.log(level, '%s', line)
    path=getattr(_LOCAL,'path',None)
    if path:
        try:
            with path.open('a',encoding='utf-8') as stream:stream.write(line+'\n')
        except OSError:
            _LOG.warning('Could not append processing trace at %s',path)


def pipeline_scope(function):
    """Clean up standalone traces even on failure; API keeps its completion context."""
    from functools import wraps
    @wraps(function)
    def wrapped(*args, **kwargs):
        previous = dict(_LOCAL.__dict__)
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            if active():
                import traceback
                event("pipeline.failed", error=str(exc), traceback=traceback.format_exc())
            raise
        finally:
            if kwargs.get("diagnostic_job_id") is None:
                _LOCAL.__dict__.clear()
                _LOCAL.__dict__.update(previous)
    return wrapped
