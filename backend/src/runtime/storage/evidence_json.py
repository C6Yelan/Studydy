"""JSONB 無法保存 U+0000；僅在必要時無損編碼，讀回後才做來源／hash 驗證。"""
import re
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

_MARKER = '_studydy_json_storage'
_ESCAPE = '\ue000'


def _has_nul(value):
    if isinstance(value,str):return '\x00' in value
    if isinstance(value,list):return any(_has_nul(v) for v in value)
    if isinstance(value,dict):return any(_has_nul(k) or _has_nul(v) for k,v in value.items())
    return False


def _strings(value,transform):
    if isinstance(value,str):return transform(value)
    if isinstance(value,list):return [_strings(v,transform) for v in value]
    if isinstance(value,dict):return {transform(k):_strings(v,transform) for k,v in value.items()}
    return value


class EvidenceJSONB(TypeDecorator):
    """不改 canonical document；schema/revision 仍可由 DB 原有約束核對。"""
    impl=JSONB
    cache_ok=True

    def __init__(self, *, none_as_null=True):
        super().__init__(none_as_null=none_as_null)

    def process_bind_param(self,value,dialect):
        if isinstance(value,dict) and _MARKER in value:
            raise ValueError('JSON_STORAGE_MARKER_RESERVED')
        if not _has_nul(value):return value
        encoded=_strings(value,lambda s:s.replace(_ESCAPE,_ESCAPE+'E').replace('\x00',_ESCAPE+'0'))
        if isinstance(value,dict):return {**encoded,_MARKER:'nul-object/v1'}
        if isinstance(value,list):return {_MARKER:'nul-array/v1','items':encoded}
        raise ValueError('JSON_STORAGE_SHAPE_INVALID')

    def process_result_value(self,value,dialect):
        if not isinstance(value,dict) or _MARKER not in value:return value
        kind=value[_MARKER]
        if kind=='nul-object/v1':payload={k:v for k,v in value.items() if k!=_MARKER}
        elif kind=='nul-array/v1':payload=value['items']
        else:raise ValueError('JSON_STORAGE_ENCODING_INVALID')
        return _strings(payload,lambda s:re.sub(_ESCAPE+'([E0])',lambda m:_ESCAPE if m[1]=='E' else '\x00',s))

    def coerce_compared_value(self,op,value):
        # 保留 JSONB 路徑與 contains 的原生操作，不能把欄位名編成 JSON 字串。
        return self.impl.coerce_compared_value(op,value)
