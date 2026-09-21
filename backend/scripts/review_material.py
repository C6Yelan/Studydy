"""離線整理已保存的 source-aware KS view；不寫產品 DB、不重跑抽取或出題。"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import stat
import time
import knowledge_map.material_review as review_implementation

from knowledge_map.material_review import (POLICY, PROMPT, ReviewError, prepare_review,
    project_review, response_schema, review_runtime_lock)
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.command_semantics import retain_call_outputs, settings
from runtime.semantic_service import SemanticServiceError, request_semantics, semantic_client


REPO = Path(__file__).resolve().parents[2]


def private_output(path: Path) -> Path:
    resolved = path.resolve()
    allowed = [(REPO/'.studydy-runtime').resolve(), (REPO.parent/'.studydy-product/experiments').resolve()]
    if not any(root in resolved.parents for root in allowed) and not (resolved.parent==Path('/tmp') and resolved.name.startswith('studydy-')):
        raise ReviewError('REVIEW_OUTPUT_NOT_PRIVATE')
    # 不覆寫既有呼叫或失敗證據；新的明確執行必須使用新目錄。
    resolved.mkdir(mode=0o700,parents=True,exist_ok=False)
    if stat.S_IMODE(resolved.stat().st_mode)!=0o700:
        raise ReviewError('REVIEW_OUTPUT_NOT_PRIVATE')
    return resolved


def save(path: Path, value: dict) -> None:
    with path.open('x',encoding='utf-8') as stream:
        os.chmod(path,0o600)
        json.dump(value,stream,ensure_ascii=False,indent=2)


def preview(view: dict, unit, projection: dict) -> str:
    names={c['concept_id']:c['label'] for c in view['concepts']}
    claims={q['claim_id']:q for c in view['concepts'] for q in c['claims']}
    owners={q['claim_id']:c['label'] for c in view['concepts'] for q in c['claims']}
    messages={'PROTECTED_TOPIC_OR_PREREQUISITE':'與原章節標題或先備關係綁定，先保留待核對',
              'PARENT_KEPT_AS_LEARNING_UNIT':'保留為下層重點的父觀念，不繼續上併',
              'CLAIM_CORRECTION_NOT_SUPPORTED':'補正未通過字面值或來源檢查，保留原文稿',
              'CLAIM_REFERENT_NOT_CITED':'補正提到未引用來源的其他概念，保留原文稿',
              'EXAMPLE_DIRECTION_OR_TYPE_NEEDS_REVIEW':'案例歸屬與原例子關係不一致，需核對型別與方向',
              'PREREQUISITE_REMOVAL_REQUIRES_REVIEW':'先備關係的移除需另核對，本次保留'}
    selected={c['concept_id'] for c in unit.concepts}
    clean=lambda text:str(text).replace('|','／').replace('\n',' ')
    lines=['# 教材整理候選', '', '**需對照原文審查；尚未套用產品，原始資料不變。**', '',
           '| 學習單元 | 包含原觀念／重點 | 附屬案例 |', '|---|---|---|']
    for item in projection['learning_units']:
        if not selected.intersection(item['member_concept_ids']+item['example_concept_ids']):continue
        lines.append('| '+clean(item['label'])+' | '+clean('、'.join(names[k] for k in item['member_concept_ids']))+' | '+clean('、'.join(names[k] for k in item['example_concept_ids']))+' |')
    lines+=['','## 背景資訊','', '、'.join(names[k] for k in projection['metadata_concept_ids']) or '無。', '', '## 別名更正','']
    for change in projection['alias_changes']:
        lines.append(f"- {names[change['concept_id']]}：建議移除 {', '.join(change['remove'])}。{change['reason']}")
    if not projection['alias_changes']:lines.append('無。')
    lines+=['','## 文字更正','']
    for change in projection['claim_changes']:
        lines += [f"原文稿：{clean(claims[change['original_claim_id']]['text'])}",'',f"提案：{change['proposed_text']}",'',f"理由：{change['reason']}",'']
    if not projection['claim_changes']:lines.append('無。')
    lines+=['','## 保留待確認／被阻擋的修改','']
    for item in projection['blocked_changes']+projection['findings']:
        key=item.get('concept_id',item.get('claim_id',item.get('relation_id','')))
        lines.append(f"- {names.get(key,owners.get(key,key))}：{messages.get(item['reason'],item['reason'])}"+
                     (f"（涉及：{'、'.join(item['concepts'])}）" if item.get('concepts') else ''))
    if not projection['blocked_changes'] and not projection['findings']:lines.append('沒有結構檢查阻擋，但內容仍需審查。')
    lines+=['','所有來源、案例與原 Claims 仍保留於原始 JSON；本檔不代表模型內容已通過驗收。','']
    return '\n'.join(lines)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--view',required=True,type=Path)
    parser.add_argument('--source-id',required=True)
    parser.add_argument('--pages',required=True,help='同一來源的完整小節頁段，例如 14:21')
    parser.add_argument('--title',required=True)
    parser.add_argument('--output',required=True,type=Path)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--generate',action='store_true',help='明確呼叫已設定的模型一次，不自動重試')
    action.add_argument('--response',type=Path,help='只重驗既有回應，不呼叫模型')
    args=parser.parse_args()
    output=None
    started=time.monotonic()
    try:
        first,last=map(int,args.pages.split(':'))
        view=json.loads(args.view.read_text())
        unit=prepare_review(view,args.source_id,first,last,title=args.title)
        output=private_output(args.output)
        save(output/'request.json',unit.payload)
        save(output/'response-schema.json',response_schema())
        binding={'policy':POLICY,'policy_sha256':canonical_sha256(PROMPT),
                 'implementation_sha256':canonical_sha256(Path(review_implementation.__file__).read_text()),
                 'source_file':str(args.view.resolve()),'source_sha256':unit.source_digest,
                 'source_revision':view['knowledge_structure_revision'],'source_id':args.source_id,
                 'pages':[first,last],'title':args.title,
                 'concept_ids':[c['concept_id'] for c in unit.concepts],
                 'claim_ids':[q['claim_id'] for q in unit.claims],
                 'evidence_ids':[e['evidence_id'] for e in unit.evidence],
                 'relation_ids':[r['relation_id'] for r in unit.relations]}
        save(output/'binding.json',binding)
        if not args.generate and args.response is None:
            save(output/'result.json',{'status':'PREPARED','model_calls':0})
            print(json.dumps({'status':'PREPARED','concepts':len(unit.concepts),'evidence':len(unit.evidence)}))
            return 0
        model_calls=0
        if args.generate:
            base=json.loads((REPO/'local_ai/runtime-lock.json').read_text())
            configured=settings()
            runtime={'model_id':configured['model_id'] if configured else base['semantic_service']['model_id'],
                     'model_revision':configured['model_revision'] if configured else base['semantic_service']['revision'],
                     'transport':'command' if configured else 'http',
                     'base_runtime_lock_sha256':canonical_sha256(base)}
            save(output/'runtime.json',runtime)
            model_calls=1
            save(output/'attempt.json',{'started_at':datetime.now(UTC).isoformat(),'model_calls':model_calls})
            with retain_call_outputs(output), semantic_client() as client:
                response=request_semantics(client,runtime_lock=review_runtime_lock(base),task='material_review',
                                           request=unit.payload,response_schema=response_schema())
            # command transport 本身會保存原始 response.json；HTTP 也保留同樣證據。
            if not (output/'response.json').exists():save(output/'response.json',response)
        else:
            previous=json.loads((args.response.parent/'binding.json').read_text())
            for field in ['source_sha256','source_revision','source_id','pages','concept_ids','claim_ids','evidence_ids','relation_ids']:
                if previous[field]!=binding[field]:raise ReviewError('REVIEW_RESPONSE_BINDING_INVALID')
            response=json.loads(args.response.read_text())
            save(output/'response.json',response)
            save(output/'reused-response.json',{'path':str(args.response.resolve()),'response_sha256':canonical_sha256(response),
                                               'original_policy_sha256':previous['policy_sha256'],'model_calls':0})
        projection=project_review(view,unit,response)
        save(output/'projection.json',projection)
        with (output/'preview.md').open('x',encoding='utf-8') as stream:
            os.chmod(output/'preview.md',0o600)
            stream.write(preview(view,unit,projection))
        if canonical_sha256(json.loads(args.view.read_text()))!=unit.source_digest:
            raise ReviewError('REVIEW_SOURCE_CHANGED')
        result={'status':'STRUCTURE_CHECKED_CONTENT_REVIEW_REQUIRED','model_calls':model_calls,
                'elapsed_seconds':round(time.monotonic()-started,3),
                'original_concepts':len(view['concepts']),'reviewed_concepts':len(unit.concepts),
                'learning_units':len(projection['learning_units']),
                'grouped_concepts':sum(len(u['member_concept_ids'])-1 for u in projection['learning_units']),
                'example_concepts':sum(len(u['example_concept_ids']) for u in projection['learning_units']),
                'metadata_concepts':len(projection['metadata_concept_ids']),
                'blocked_changes':len(projection['blocked_changes']),
                'findings':len(projection['findings']),
                'alias_changes':len(projection['alias_changes']),'claim_changes':len(projection['claim_changes']),
                'checks':projection['checks']}
        save(output/'result.json',result)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (ReviewError,SemanticServiceError) as error:
        reason=str(error)
    except (OSError,ValueError,KeyError,TypeError) as error:
        reason=type(error).__name__
    result={'status':'FAILED','reason':reason,'elapsed_seconds':round(time.monotonic()-started,3)}
    if output is not None and not (output/'result.json').exists():save(output/'result.json',result)
    print(json.dumps(result))
    return 1


if __name__=='__main__':raise SystemExit(main())
