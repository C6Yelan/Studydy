import {Line, Rect, Txt, makeScene2D} from '@motion-canvas/2d';
import {all, createRef, waitFor, linear} from '@motion-canvas/core';
import timing from './timing.json';

export default makeScene2D(function* (view) {
  view.fill('#f4f8fc');
  const packet=createRef<Rect>(), packetText=createRef<Txt>(), client=createRef<Txt>(), server=createRef<Txt>(), narration=createRef<Txt>();
  view.add(<>
    <Txt text="TCP 三向握手" y={-275} fontSize={48} fill="#193858" />
    <Txt text="Client" x={-380} y={-190} fontSize={36} fill="#193858" />
    <Txt text="Server" x={380} y={-190} fontSize={36} fill="#193858" />
    <Line points={[[-380,-145],[-380,155]]} stroke="#b5c9dc" lineWidth={4}/>
    <Line points={[[380,-145],[380,155]]} stroke="#b5c9dc" lineWidth={4}/>
    <Txt ref={client} text="CLOSED" x={-380} y={200} fontSize={27} fill="#356983" />
    <Txt ref={server} text="LISTEN" x={380} y={200} fontSize={27} fill="#356983" />
    <Rect ref={packet} zIndex={10} width={160} height={64} radius={16} fill="#2f78a6" opacity={0}><Txt ref={packetText} fill="#fff" fontSize={27}/></Rect>
    <Txt ref={narration} text="" y={285} fontSize={26} fill="#193858" />
  </>);
  const labels=['SYN','SYN + ACK','ACK'];
  const captions=['用戶端提出連線要求','伺服器回覆同步與確認','用戶端確認，連線建立'];
  for(let i=0;i<3;i++) {
    const reverse=i===1, y=-95+i*85, from=reverse?380:-380, to=-from;
    const arrow=createRef<Line>(), label=createRef<Txt>();
    view.add(<Line ref={arrow} points={[[from,y],[to,y]]} stroke="#7aa5bf" lineWidth={3} endArrow end={0}/>);
    view.add(<Txt ref={label} text={labels[i]} x={0} y={y-24} fontSize={23} fill="#356983" opacity={0}/>);
    packet().position([from,y]);packet().opacity(1);packetText().text(labels[i]);narration().text(captions[i]);
    if(i===0)client().text('SYN-SENT');
    if(i===2)client().text('ESTABLISHED');
    const duration=timing.durations[i];
    yield* waitFor(.2);
    yield* all(packet().x(to,Math.max(.5,duration-.6),linear),arrow().end(1,Math.max(.5,duration-.6),linear));
    if(i===0)server().text('SYN-RECEIVED');
    if(i===2)server().text('ESTABLISHED');
    yield* all(packet().opacity(0,.2),label().opacity(1,.2));
    yield* waitFor(.2);
  }
  yield* all(client().fill('#187050',.3),server().fill('#187050',.3));
  yield* waitFor(.7);
});
