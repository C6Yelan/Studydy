import {Renderer, Vector2} from '@motion-canvas/core';
export async function editor(project) {
  const renderer=new Renderer(project);
  document.body.append(renderer.stage.finalBuffer);
  project.logger.onLogged.subscribe(value=>{if(value.level==='error')window.renderError=value.message;});
  renderer.onFinished.subscribe(result=>{window.renderResult=result;});
  window.renderReady=true;
  window.renderMovie=async()=>{
    await renderer.render({...project.meta.getFullRenderingSettings(),name:'handshake',size:new Vector2(1280,720),fps:30,resolutionScale:1,range:[0,Infinity],background:'#f4f8fc',exporter:{name:'@motion-canvas/ffmpeg',options:{fastStart:true,includeAudio:true}}});
  };
}
