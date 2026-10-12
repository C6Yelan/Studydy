"""有界並行子圖：各批獨立生成，單一協調者合併，保存亂序完成範圍。"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Event, Lock

from knowledge_map.semantic_projection import SemanticState, build_semantic_bundles
from knowledge_map.independent_blocks import block_request, block_schema, project_block, merge_block
from runtime.semantic_service import semantic_client, SemanticServiceError
from .ocr_page_evidence import canonical_sha256


def analyze_blocks(context, state, progress, *, lock, fits, semantic_call, archive,
                   checkpoint, report, check_cancel, minimum_page=1):
    """progress 的 cursor 是連續前綴；ranges 保存已合併但前面仍有缺口的範圍。"""
    size = len(context['evidence'])
    cursor = progress['cursor']
    if type(cursor) is not int or not 0 <= cursor <= size:
        raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
    ranges = {start: end for start, end in progress.get('ranges', [])}
    if len(ranges) != len(progress.get('ranges', [])):
        raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
    previous = cursor
    for start, end in sorted(ranges.items()):
        if type(start) is not int or type(end) is not int or not previous <= start < end <= size:
            raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
        previous = end
    task = lock['material_semantics']
    handles = {e['evidence_id']: i for i, e in enumerate(context['evidence'])}
    stream = iter(build_semantic_bundles(context, state=SemanticState(), fits=fits,
                  minimum_page=minimum_page, minimum_evidence_index=cursor))
    stopped = Event()
    call_lock = Lock()
    calls = progress['semantic_calls']

    def check():
        check_cancel()
        if stopped.is_set():
            raise ValueError('ANALYSIS_BLOCK_INTERRUPTED')

    def generate(job):
        nonlocal calls
        with semantic_client() as client:
            for attempt in range(task['retry_attempts']):
                check()
                with call_lock:
                    calls += 1
                    number = calls
                if archive is not None:
                    archive.prepare_block_call(job['id'], number, job['request'])
                try:
                    response = semantic_call(client, runtime_lock=lock, task='material_semantics',
                                             request=job['request'], response_schema=job['schema'], cancellation_check=check)
                    if archive is not None:
                        archive.save_block_response(job['id'], number, response)
                    block = project_block(context, job['bundle'], job['id'], response)
                    if archive is not None:
                        archive.complete_block(job['id'], job['digest'], number, response)
                    return block, number
                except (SemanticServiceError, ValueError):
                    check()
                    if attempt + 1 == task['retry_attempts']:
                        raise

    def saved():
        nonlocal cursor
        while cursor in ranges:
            cursor = ranges.pop(cursor)
        progress.update(cursor=cursor, ranges=sorted(ranges.items()), semantic_calls=calls)
        checkpoint()
        report('semantics', cursor + sum(end - start for start, end in ranges.items()), size)

    def merge(job, block, number):
        nonlocal calls
        merge_block(state, block)
        ranges[job['start']] = job['end']
        with call_lock:
            calls = max(calls, number)
        saved()

    pending, exhausted, plan_error, failure = {}, False, None, None
    plan_cursor = cursor
    pool = ThreadPoolExecutor(max_workers=task['max_concurrency'], thread_name_prefix='material-block')
    try:
        while pending or not exhausted:
            check_cancel()
            while not exhausted and failure is None and len(pending) < task['max_concurrency']:
                try:
                    bundle = next(stream)
                except StopIteration:
                    exhausted = True
                    break
                except ValueError as error:
                    plan_error, exhausted = error, True
                    break
                end = max(handles[e['evidence_id']] for e in bundle['evidence']) + 1
                start, plan_cursor = plan_cursor, end
                if end <= cursor:
                    continue
                if start < cursor:
                    raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
                if start in ranges:
                    if ranges[start] != end:
                        raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
                    continue
                if any(start < stop and begin < end for begin, stop in ranges.items()):
                    raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
                request = block_request(context, bundle)
                schema = block_schema([row[0] for s in request['sections'] for row in s['evidence']])
                # 不把併行數放入輸入身分，調低併行上限不會作廢已完成批次。
                signature = canonical_sha256({'request': request, 'schema': schema,
                    'task': {k: v for k, v in task.items() if k not in ('max_concurrency', 'max_tokens')}})
                job = {'start': start, 'end': end, 'id': signature, 'digest': signature,
                       'request': request, 'schema': schema, 'bundle': bundle}
                cached = archive.load_block_response(signature, signature) if archive is not None else None
                if cached is not None:
                    merge(job, project_block(context, bundle, signature, cached['response']), cached['call_number'])
                    continue
                pending[pool.submit(generate, job)] = job
            if not pending:
                break
            done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
            for future in done:
                job = pending.pop(future)
                try:
                    block, number = future.result()
                except Exception as error:
                    failure = failure or error
                    exhausted = True
                    continue
                merge(job, block, number)
        if failure is not None:
            raise failure
        if plan_error is not None:
            # 所有先前請求已收完，呼叫者才可安全修復尚未處理的超額 OCR 區塊。
            raise plan_error
        if ranges:
            raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
        progress['cursor'] = size  # 結尾純非內容區塊也已完成分類。
        progress['semantic_calls'] = calls
        return True
    finally:
        stopped.set()
        pool.shutdown(wait=True, cancel_futures=True)
        progress.update(cursor=progress['cursor'], semantic_calls=calls)
