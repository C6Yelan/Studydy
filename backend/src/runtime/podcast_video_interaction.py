"""有來源支持的雙方訊息演示：物件沿實測講解時序移動，狀態完全由媒體時間重建。"""
from .podcast_video_motion import ease, window


def interaction_state(t, timeline, page):
    groups = page['motion']['groups']
    actors = [i for i, g in enumerate(groups) if 'source' not in g]
    messages = [i for i, g in enumerate(groups) if 'source' in g]
    ends = timeline['segments'][page['end_cue']]['end']
    states = {i: '等候訊息' for i in actors}
    events = []
    for position, i in enumerate(messages):
        message = groups[i]
        start, anchor_end = window(message, timeline)
        # 每個訊息在自己的實測區間內演示一次，不自行重複成額外封包／重送。
        stop = min(anchor_end, window(groups[messages[position+1]], timeline)[0]
                   if position+1 < len(messages) else ends)
        phase = min(1., max(0., (t-start) / (stop-start)))
        if t < start:
            continue
        stage = 'sending' if phase < .18 else 'transit' if phase < .78 else message['outcome']
        states[message['source']] = '傳送中' if phase < .78 else '已送出'
        states[message['target']] = ('已接收' if stage == 'delivered' else
                                     '未收到此訊息' if stage == 'lost' else '等候訊息')
        events.append({'group': i, 'phase': phase, 'stage': stage,
                       'position': ease((phase-.18)/.60), 'start': start, 'stop': stop})
    return {'actors': states, 'events': events, 'current': events[-1] if events else None}


def draw_interaction(image, t, timeline, page):
    from PIL import ImageDraw
    from .podcast_video_render import font, lines, PALETTE
    draw = ImageDraw.Draw(image, 'RGBA')
    groups = page['motion']['groups']
    state = interaction_state(t, timeline, page)
    actors = list(state['actors'])
    centers = {actors[0]: 350, actors[1]: 1570}
    ink, teal, muted = PALETTE['ink'], PALETTE['teal'], PALETTE['muted']

    def centered(text, cx, y, size=34, width=510):
        for line in lines(text, size, width):
            draw.text((cx-font(size).getlength(line)/2, y), line, font=font(size), fill=ink)
            y += size+10

    for i in actors:
        group = groups[i]; x = centers[i]; y = 440
        element = page['elements'][group['elements'][0]]
        centered(element['text'], x, 230, 38)
        active = state['actors'][i] in ('傳送中', '已接收')
        color = teal if active else muted
        # 都是可重用物件形狀；圖示由來源分鏡選擇，不依概念名稱或指定教材判斷。
        if group['icon'] == 'computer':
            draw.rounded_rectangle((x-125,y-90,x+125,y+65),radius=12,fill='#edf3f5',outline=color,width=5)
            draw.rectangle((x-108,y-73,x+108,y+44),fill='#d6eee9' if active else '#e2e8eb')
            draw.line((x,y+66,x,y+102),fill=color,width=9)
            draw.line((x-70,y+104,x+70,y+104),fill=color,width=8)
        elif group['icon'] == 'server':
            draw.rounded_rectangle((x-100,y-105,x+100,y+105),radius=14,fill='#edf3f5',outline=color,width=5)
            for row in range(3):
                top=y-83+row*62
                draw.rounded_rectangle((x-80,top,x+80,top+42),radius=5,outline=color,width=3)
                draw.ellipse((x+52,top+14,x+65,top+27),fill=teal if active else '#abbac1')
                draw.line((x-58,top+21,x+28,top+21),fill=color,width=3)
        elif group['icon'] == 'mailbox':
            draw.rounded_rectangle((x-120,y-70,x+120,y+75),radius=24,fill='#edf3f5',outline=color,width=5)
            draw.line((x-88,y-24,x+88,y-24),fill=color,width=7)
            draw.line((x,y+77,x,y+112),fill=color,width=9)
        else:
            draw.ellipse((x-100,y-100,x+100,y+100),fill='#edf3f5',outline=color,width=5)
            for dx,dy in ((-35,-25),(35,-25),(0,35)):
                draw.ellipse((x+dx-12,y+dy-12,x+dx+12,y+dy+12),fill=color)
            draw.line((x-35,y-25,x+35,y-25,x,y+35,x-35,y-25),fill=color,width=3)
        centered(state['actors'][i], x, 582, 32)

    draw.line((505,440,1415,440),fill='#c8d6dc',width=4)
    current = state['current']
    if current:
        group = groups[current['group']]
        label = next(page['elements'][j]['text'] for j in group['elements'] if page['elements'][j]['kind']=='text')
        centered(label,960,300,34,820)
        left = centers[group['source']] < centers[group['target']]
        a,b = (520,1400) if left else (1400,520)
        position = current['position']
        lost = group['outcome']=='lost'
        x = a+(b-a)*position*(.6 if lost else 1)
        y = 440+(70*ease((current['phase']-.68)/.18) if lost else 0)
        if current['stage'] in ('sending','transit'):
            draw.line((a,440,x,440),fill=teal,width=6)
            draw.rounded_rectangle((x-46,y-28,x+46,y+28),radius=8,fill='#e4f5ef',outline=teal,width=4)
            draw.line((x-32,y-15,x,y+7,x+32,y-15),fill=teal,width=3)
            draw.line((x-28,y+17,x+28,y+17),fill=teal,width=2)
        elif current['stage']=='lost':
            draw.line((x-20,y-20,x+20,y+20),fill=PALETTE['orange'],width=6)
            draw.line((x+20,y-20,x-20,y+20),fill=PALETTE['orange'],width=6)
        else:
            draw.ellipse((b-24,416,b+24,464),fill='#d6eee9',outline=teal,width=3)
            draw.line((b-12,440,b-3,450,b+14,429),fill=teal,width=4)
        explanation = {'sending':'準備送出', 'transit':'訊息傳遞中', 'delivered':'接收端收到此訊息', 'lost':'此訊息未到達接收端'}[current['stage']]
        centered(explanation,960,675,36,1100)
    else:
        centered('等待講解中的第一個訊息',960,675,34,1100)
    # 完成的步驟留下結果；往回 seek 時從媒體時間重新建立，沒有累積狀態。
    for position, i in enumerate(g for g in range(len(groups)) if 'source' in groups[g]):
        x=800+position*64
        event=next((e for e in state['events'] if e['group']==i),None)
        color=teal if event and event['stage']=='delivered' else PALETTE['orange'] if event and event['stage']=='lost' else '#c8d6dc'
        draw.ellipse((x-16,770,x+16,802),fill=color)
        draw.text((x-font(20).getlength(str(position+1))/2,772),str(position+1),font=font(20),fill='white')
    draw.text((100,805),'流程示意・移動速度不代表實際網路延遲',font=font(22),fill=muted)
    return image
