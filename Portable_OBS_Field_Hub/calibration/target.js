const ui=document.querySelector("#ui"),go=document.querySelector("#go"),statusLine=document.querySelector("#status");
go.onclick=()=>{
 ui.hidden=true; const begin=performance.now(), marks=[6,13,22,28,36];
 function tick(now){
  const elapsed=(now-begin)/1000;
  document.body.classList.toggle("white",marks.some(t=>elapsed>=t && elapsed<t+.75));
  if(elapsed<45){requestAnimationFrame(tick);return}
  document.body.classList.remove("white");ui.hidden=false;statusLine.textContent="Finished. Stop the local recording.";
 }
 requestAnimationFrame(tick);
};
