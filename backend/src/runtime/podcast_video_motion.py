"""v3 的有限動態語法：只使用編譯後群組、既有箭頭與實測時間，不累積影格狀態。"""

import math
from .podcast_video_plan import visual_window


def ease(value):
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


def window(item, timeline):
    return visual_window(
        timeline, item["start_cue"], item["start_cue"], item.get("caption_index")
    )


def progress(t, start, end, maximum):
    # 短字幕也必須在自己的實測區間內完成；固定上限是視覺節奏，並非教材中的速度。
    return ease((t - start) / min(maximum, (end - start) / 3))


def validate_motion(page, where):
    motion = page["motion"]
    if (
        not isinstance(motion, dict)
        or set(motion) != {"layout", "groups"}
        or motion["layout"] not in ("concept", "comparison", "flow", "exchange")
        or not isinstance(motion["groups"], list)
        or not 1 <= len(motion["groups"]) <= 12
        or any(not isinstance(group, dict) for group in motion["groups"])
    ):
        raise ValueError(where + ", invalid motion groups")
    groups = motion["groups"]
    used = []
    nodes = [
        i for i, g in enumerate(groups) if isinstance(g, dict) and "source" not in g
    ]
    for i, group in enumerate(groups):
        fields = {"elements", "start_cue"} | (
            {"source", "target"} if "source" in group else set()
        )
        if (
            not isinstance(group, dict)
            or set(group) not in (fields, fields | {"caption_index"})
            or type(group["start_cue"]) is not int
            or not page["start_cue"] <= group["start_cue"] <= page["end_cue"]
            or (
                "caption_index" in group
                and (
                    type(group["caption_index"]) is not int
                    or group["caption_index"] < 0
                )
            )
            or not isinstance(group["elements"], list)
            or not group["elements"]
            or any(
                type(j) is not int or not 0 <= j < len(page["elements"])
                for j in group["elements"]
            )
        ):
            raise ValueError(where + ", invalid motion anchor or element")
        used.extend(group["elements"])
        elements = [page["elements"][j] for j in group["elements"]]
        if any(e["cue_index"] != group["start_cue"] for e in elements):
            raise ValueError(where + ", motion must follow the element explanation")
    for group in groups:
        elements = [page["elements"][j] for j in group["elements"]]
        if "source" not in group:
            # v4 比較欄把標題與說明分成兩個文字元素，說明才能用一般墨色標重點詞。
            if elements[0]["kind"] not in ("text", "box", "circle") or any(
                e["kind"] not in ("line", "text") for e in elements[1:]
            ):
                raise ValueError(
                    where
                    + ", motion node must contain a labelled shape and optional text or guides"
                )
            continue
        if (
            motion["layout"] not in ("flow", "exchange")
            or any(
                type(group[k]) is not int or group[k] not in nodes
                for k in ("source", "target")
            )
            or group["source"] == group["target"]
            or (motion["layout"] == "flow" and group["target"] != group["source"] + 1)
            or sum(e["kind"] == "arrow" for e in elements) != 1
            or any(e["kind"] not in ("text", "arrow") for e in elements)
        ):
            raise ValueError(where + ", motion requires an existing directed relation")
        arrow = next(e for e in elements if e["kind"] == "arrow")
        for key, point in (
            ("source", (arrow["x"], arrow["y"])),
            ("target", (arrow["x"] + arrow["w"], arrow["y"] + arrow["h"])),
        ):
            # 僅核對 v3 動態綁定；舊平面分鏡的合法留白不受此限制。
            target = [page["elements"][j] for j in groups[group[key]]["elements"]]

            def distance(e):
                if e["kind"] == "line":
                    a = (e["x"], e["y"])
                    ratio = min(
                        1,
                        max(
                            0,
                            ((point[0] - a[0]) * e["w"] + (point[1] - a[1]) * e["h"])
                            / (e["w"] ** 2 + e["h"] ** 2),
                        ),
                    )
                    return math.dist(
                        point, (a[0] + ratio * e["w"], a[1] + ratio * e["h"])
                    )
                return math.hypot(
                    max(e["x"] - point[0], 0, point[0] - e["x"] - e["w"]),
                    max(e["y"] - point[1], 0, point[1] - e["y"] - e["h"]),
                )

            if min(map(distance, target)) > 24:
                raise ValueError(
                    where + ", motion direction must match the existing arrow endpoints"
                )
    if sorted(used) != list(range(len(page["elements"]))):
        raise ValueError(where + ", motion must group every element exactly once")


def validate_motion_timing(page, timeline, visible, where):
    for group in page["motion"]["groups"]:
        times = {visible[j] for j in group["elements"]}
        if len(times) != 1 or next(iter(times)) > window(group, timeline)[0]:
            raise ValueError(
                where + ", motion focus must follow its whole revealed group"
            )
        if "source" in group:
            for key in ("source", "target"):
                if any(
                    visible[j] > min(times)
                    for j in page["motion"]["groups"][group[key]]["elements"]
                ):
                    raise ValueError(
                        where + ", motion relation must not precede its nodes"
                    )


def page_window(timeline, page, bounds=None):
    """頁面起訖；未給 bounds 時以 cue 邊界計算，與未拆頁的分鏡相同。"""
    if bounds is not None:
        return bounds
    return (
        timeline["segments"][page["start_cue"]]["start"],
        timeline["segments"][page["end_cue"]]["end"],
    )


def scene_state(t, timeline, page, bounds=None):
    """同時的語意 anchor 同時聚焦；不替模型杜撰額外的教學先後。"""
    groups = page["motion"]["groups"]
    page_start, page_end = page_window(timeline, page, bounds)
    appearances = {
        j: (page_start, min(page_end, timeline["segments"][page["start_cue"]]["end"]))
        for j in range(len(page["elements"]))
    }
    for reveal in page.get("reveal", []):
        for j in reveal["elements"]:
            appearances[j] = window(reveal, timeline)
    anchors = [window(g, timeline) for g in groups]
    event_times = sorted({start for start, _ in anchors})
    # 整 cue 焦點也要在下一個細 anchor 前完成轉移，避免短字幕邊界跳動。
    anchors = [
        (start, min(end, next((later for later in event_times if later > start), end)))
        for start, end in anchors
    ]
    events = [start for start in event_times if start <= t]

    def weights(event, now):
        values = [0.0] * len(groups)
        for i, (start, end) in enumerate(anchors):
            if start != event:
                continue
            values[i] = 1.0
            if "source" in groups[i]:
                phase = progress(now, start, end, 0.7)
                for key, weight in (("source", 1 - phase), ("target", phase)):
                    target = groups[i][key]
                    values[target] = max(values[target], weight)
        return values

    focus = [1.0] * len(groups)
    if events:
        latest = events[-1]
        current = weights(latest, t)
        previous = (
            weights(events[-2], latest)
            if len(events) > 1
            else ([1.0] * len(groups) if latest > page_start else current)
        )
        end = min(end for start, end in anchors if start == latest)
        blend = progress(t, latest, end, 0.28)
        focus = [a + (b - a) * blend for a, b in zip(previous, current)]
    result = []
    for i, group in enumerate(groups):
        start, end = appearances[group["elements"][0]]
        anchor, stop = anchors[i]
        if start == anchor:
            end = min(end, stop)
        entrance = progress(t, start, end, 0.4)
        phase = progress(t, anchor, stop, 0.7)
        result.append(
            {
                "entrance": entrance,
                "focus": focus[i],
                "opacity": entrance * (0.65 + 0.35 * focus[i]),
                "draw": progress(t, start, end, 0.5),
                "token": phase
                if "source" in group and anchor < t < stop and phase < 1
                else None,
            }
        )
    return result


def validate_spotlight(page, where):
    """聚焦頁：左側章節列列出所有節點，右側同一區域依序只亮一張卡；實測起點不足時相鄰節點共用一張卡。"""
    spotlight = page["spotlight"]
    if (
        not isinstance(spotlight, dict)
        or set(spotlight) != {"layout", "steps"}
        or spotlight["layout"] not in ("concept", "flow")
        or not isinstance(spotlight["steps"], list)
        or not 1 <= len(spotlight["steps"]) <= 6
    ):
        raise ValueError(where + ", invalid spotlight steps")
    elements = page["elements"]
    used = []
    for k, step in enumerate(spotlight["steps"]):
        fields = {"start_cue", "rails", "card"}
        if (
            not isinstance(step, dict)
            or set(step) not in (fields, fields | {"caption_index"})
            or type(step["start_cue"]) is not int
            or not page["start_cue"] <= step["start_cue"] <= page["end_cue"]
            or (
                "caption_index" in step
                and (
                    type(step["caption_index"]) is not int or step["caption_index"] < 0
                )
            )
            or not isinstance(step["rails"], list)
            or not 1 <= len(step["rails"]) <= 6
            or not isinstance(step["card"], list)
            or not len(step["rails"]) + 1
            <= len(step["card"])
            <= 2 * len(step["rails"]) + 1
            or any(
                type(j) is not int or not 0 <= j < len(elements)
                for j in [*step["rails"], *step["card"]]
            )
        ):
            raise ValueError(
                where + f", spotlight step={k} has invalid anchor or elements"
            )
        rails = [elements[j] for j in step["rails"]]
        card = [elements[j] for j in step["card"]]
        if (
            any(e["kind"] != "text" for e in rails + card[1:])
            or card[0]["kind"] != "box"
            or card[0]["text"]
            or any(e["cue_index"] != step["start_cue"] for e in rails + card)
        ):
            raise ValueError(
                where
                + f", spotlight step={k} needs rail labels and a card box with text"
            )
        used += [*step["rails"], *step["card"]]
    first = spotlight["steps"][0]
    if first["start_cue"] != page["start_cue"] or first.get(
        "caption_index"
    ) != page.get("caption_index"):
        raise ValueError(where + ", the first spotlight card starts with the page")
    if sorted(used) != list(range(len(elements))):
        raise ValueError(where + ", spotlight must use every element exactly once")


def spotlight_state(t, timeline, page, bounds=None):
    """每張卡從自己的實測起點亮到下一張開始；交接時短暫交叉淡化，頁首卡直接出現。"""
    steps = page["spotlight"]["steps"]
    starts = [window(step, timeline)[0] for step in steps]
    ends = starts[1:] + [page_window(timeline, page, bounds)[1]]
    fades = [min(0.3, (end - start) / 3) for start, end in zip(starts, ends)]
    result = []
    for k, (start, end) in enumerate(zip(starts, ends)):
        arrive = 1.0 if k == 0 else ease((t - start) / fades[k]) if t >= start else 0.0
        leave = (
            ease((t - end) / fades[k + 1]) if k + 1 < len(steps) and t >= end else 0.0
        )
        result.append({"now": arrive * (1 - leave), "done": t >= end})
    return result


def transition(t, timeline, page, bounds=None):
    start, end = page_window(timeline, page, bounds)
    return progress(t, start, end, 0.2)
