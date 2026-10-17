"""獨立來源子圖：模型只使用本地索引，全域身分及來源關係由程式維護。"""
from .semantic_projection import SemanticState, semantic_request, semantic_response_schema, apply_semantic_response
from pdf_evidence.ocr_page_evidence import canonical_sha256

POLICY = 'independent-evidence-blocks/v1'


def request_view(request):
    return {key: request[key] for key in ('sections', 'source_pages', 'source_policy','learning_scopes') if key in request}


def block_request(context, bundle):
    return request_view(semantic_request(context, bundle, SemanticState()))


def block_schema(handles):
    schema = semantic_response_schema(handles)
    concept = schema['properties']['concepts']['items']
    concept['required'].remove('k')
    concept['properties'].pop('k')
    concept['properties']['l']['maxLength'] = 256
    concept['properties']['a']['items']['maxLength'] = 256
    concept['properties']['c']['minItems'] = 1
    concept['properties']['c']['items']['properties']['m']['type'] = 'string'
    concept['properties']['c']['items']['properties']['m']['maxLength'] = 4096
    relation = schema['properties']['relations']['items']
    for key in ('s', 't'):
        relation['properties'][key] = {'type': 'integer', 'minimum': 0}
    relation['properties']['e']['items'] = {'type': 'integer', 'enum': handles}
    relation['properties']['r']['maxLength'] = 1024
    return schema


def project_block(context, bundle, identity, response):
    if (not isinstance(response, dict) or set(response) != {'concepts', 'relations'}
            or not isinstance(response['concepts'], list) or not isinstance(response['relations'], list)):
        raise ValueError('SEMANTIC_OUTPUT_INVALID')

    def key(index):
        return canonical_sha256({'block': identity, 'index': index})

    concepts, relations = [], []
    for index, concept in enumerate(response['concepts']):
        if not isinstance(concept, dict) or set(concept) != {'l', 'a', 'c'}:
            raise ValueError('SEMANTIC_OUTPUT_INVALID')
        concepts.append({'k': key(index), **concept})
    for relation in response['relations']:
        if (not isinstance(relation, dict) or set(relation) != {'s', 't', 'k', 'r', 'e', 'c'}
                or type(relation['s']) is not int or type(relation['t']) is not int):
            raise ValueError('SEMANTIC_OUTPUT_INVALID')
        # 越界索引不會變成有效概念，仍由既有關係檢核拒絕。
        relations.append({**relation, 's': key(relation['s']), 't': key(relation['t'])})
    result = SemanticState()
    apply_semantic_response({'concepts': concepts, 'relations': relations}, context=context,
                            bundle={**bundle, 'catalog_keys': []}, state=result)
    return result


def merge_block(state, block):
    if any(key in state.concepts for key in block.concepts):
        raise ValueError('ANALYSIS_CHECKPOINT_INVALID')
    state.concepts.update(block.concepts)
    state.relations.extend(block.relations)
    for field in ('rejected_claims', 'rejected_relations', 'literal_repairs'):
        setattr(state, field, getattr(state, field) + getattr(block, field))
    # 不覆寫原有概念或清除人工複核提示；跨塊同義名稱允許分開保留。
    state.source_review_required |= block.source_review_required
