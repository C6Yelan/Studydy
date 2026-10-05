"""授權後的私人媒體串流；音訊與影片共用單段 Range 行為。"""
import re
from fastapi import Response
from fastapi.responses import StreamingResponse


def artifact_response(request, context, media_type):
    source=context.__enter__()
    size=source.size_bytes
    headers={'Accept-Ranges':'bytes','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}
    start,end,status=0,size-1,200
    requested=request.headers.get('range')
    if requested:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
        try:
            if not match or not any(match.groups()):raise ValueError()
            left,right=match.groups()
            if not left:
                length=int(right)
                if length<=0:raise ValueError()
                start=max(0,size-length)
            else:
                start=int(left);end=min(size-1,int(right)) if right else size-1
            if start>=size or start>end:raise ValueError()
            status=206;headers['Content-Range']=f'bytes {start}-{end}/{size}'
        except ValueError:
            context.__exit__(None,None,None)
            return Response(status_code=416,headers={**headers,'Content-Range':f'bytes */{size}'})
    headers['Content-Length']=str(end-start+1)
    def chunks():
        try:
            source.file.seek(start);remaining=end-start+1
            while remaining:
                chunk=source.file.read(min(65536,remaining))
                if not chunk:break
                remaining-=len(chunk);yield chunk
        finally:context.__exit__(None,None,None)
    return StreamingResponse(chunks(),status_code=status,media_type=media_type,headers=headers)
