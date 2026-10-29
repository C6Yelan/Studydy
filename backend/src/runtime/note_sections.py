"""由已保存的原教材標題規劃筆記；導覽結構不冒充正文或來源。"""
import re


def _heading_path(heading, chapter):
    numbered = re.match(r"^(?:(?:Chapter|Section)\s+)?(\d+(?:\.\d+)*)(?:$|[.、:：)\s]|(?=[^\d.]))", heading, re.I)
    if numbered:
        return tuple(str(int(part)) for part in numbered[1].split("."))
    chinese = re.match(r"^第\s*([零〇一二三四五六七八九十百千\d]+)\s*([章節节])", heading)
    if not chinese:
        return None
    text = chinese[1]
    if text.isdecimal():
        number = int(text)
    else:
        number, digit = 0, 0
        for char in text:
            if char in "十百千":
                number += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                digit = 0
            else:
                digit = "零一二三四五六七八九".index(char.replace("〇", "零"))
        number += digit
    return (*chapter, str(number)) if chinese[2] != "章" and chapter else (str(number),)


def plan(document, concepts, regions):
    """每個重點按最早的直接 Evidence 歸屬一節，不拆改其完整引用。"""
    sources = document["input_binding"]["manifest"]["items"]
    pages = document["input_binding"]["bundle"]["pages"]
    evidence = {item["evidence_id"]: item for item in document["evidence"]}
    source_order = {source["source_id"]: index for index, source in enumerate(sources)}
    sections = sorted(document["document_tree"]["sections"], key=lambda section: section["order"])
    source_sections = {source["source_id"]: [] for source in sources}
    section_sources = {}
    for item in document["evidence"]:
        section_sources[item["section_id"]] = pages[item["page"] - 1]["source_id"]
    for section in sections:
        source_sections[section_sources[section["section_id"]]].append(section)
    claims = {claim["claim_id"]: claim for concept in concepts for claim in concept["claims"]}
    if len(sources) == 1 and not any(section["heading_evidence_id"] for section in sections):
        by_concept = {concept["concept_id"]: concept for concept in concepts}
        assigned, result = set(), []
        for index, region in enumerate(regions):
            ids = list(dict.fromkeys(claim["claim_id"] for cid in region["concept_ids"]
                for claim in by_concept[cid]["claims"] if claim["claim_id"] not in assigned))
            assigned.update(ids)
            result.append({"section_id": region.get("region_id", f"map-section-{index}"),
                "heading": region["label"], "level": 1, "parent_id": None, "claim_ids": ids})
        return result

    result, destination = [], {}
    multiple = len(sources) > 1
    for source in sources:
        file_section = None
        if multiple:
            file_section = {"section_id": f'source-{source["source_id"]}',
                "heading": source["original_name"], "level": 1, "parent_id": None, "claim_ids": []}
            result.append(file_section)
        stack, chapter = [], None
        current = file_section
        for section in source_sections[source["source_id"]]:
            if not section["heading_evidence_id"]:
                if current is None:
                    # 第一個真標題之前的正文保留既存「教材開頭」，不掛到後面的章。
                    current = {"section_id": section["section_id"], "heading": section["title"],
                        "level": 1, "parent_id": None, "claim_ids": []}
                    result.append(current)
                destination[section["section_id"]] = current
                continue
            path = _heading_path(section["title"], chapter)
            if path is None:
                stack, chapter = [], None
            else:
                while stack and (path[:len(stack[-1][0])] != stack[-1][0]
                                 or len(path) <= len(stack[-1][0]) or stack[-1][1]["level"] >= 3):
                    stack.pop()
                if not stack:
                    chapter = path
            parent = stack[-1][1] if stack else file_section
            current = {"section_id": section["section_id"], "heading": section["title"],
                "level": parent["level"] + 1 if parent else 1,
                "parent_id": parent["section_id"] if parent else None, "claim_ids": []}
            result.append(current)
            destination[section["section_id"]] = current
            if path is not None:
                stack.append((path, current))

    def position(reference):
        item = evidence[reference["evidence_id"]]
        page = pages[item["page"] - 1]
        return source_order[page["source_id"]], page["normalized_page"], item["block_order"]

    for identity, claim in sorted(claims.items(), key=lambda item: min(position(ref) for ref in item[1]["evidence"])):
        first = min(claim["evidence"], key=position)
        section_id = evidence[first["evidence_id"]]["section_id"]
        destination[section_id]["claim_ids"].append(identity)
    return result


def compact_sections(sections):
    """速覽省略無正文小節，保留原文父章與順序；一級檔名章仍可辨認缺漏。"""
    by_id = {section["section_id"]: section for section in sections}
    keep = {section["section_id"] for section in sections if section["level"] == 1 or section["paragraph_ids"]}
    for identity in list(keep):
        parent = by_id[identity]["parent_id"]
        while parent is not None:
            keep.add(parent)
            parent = by_id[parent]["parent_id"]
    return [{**section, "outline": [item for item in section["outline"] if item["section_id"] in keep]}
        for section in sections if section["section_id"] in keep]


def published(sections, paragraphs, concepts, *, kind="full"):
    """章節大綱與缺漏清單只從本次已核對正文投影，不再請模型整理。"""
    result = [{key: section[key] for key in ("section_id", "heading", "level", "parent_id")}
              | {"paragraph_ids": [], "outline": []} for section in sections]
    by_claim = {identity: output for section, output in zip(sections, result) for identity in section["claim_ids"]}
    for paragraph in paragraphs:
        # 生成批次已限制為單節；保留正文 id，讓註記與地圖連結仍指向同一段。
        by_claim[paragraph["claim_ids"][0]]["paragraph_ids"].append(paragraph["id"])
    chapter = None
    for section in result:
        if section["level"] == 1:
            chapter = section
        else:
            chapter["outline"].append({key: section[key] for key in ("section_id", "heading", "level")})
    explained = {identity for paragraph in paragraphs
                 for identity in paragraph.get("_explained_claim_ids", paragraph["claim_ids"])}
    missing = [{"concept_id": concept["concept_id"], "label": concept["label"]}
               for concept in concepts if not any(claim["claim_id"] in explained for claim in concept["claims"])]
    return {"sections": compact_sections(result) if kind == "summary" else result, "unorganized_concepts": missing}
