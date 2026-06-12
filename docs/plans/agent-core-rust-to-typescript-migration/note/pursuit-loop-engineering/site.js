/* shared chrome: section reveal · offscreen animation pause · rail scrollspy */
const reveal = new IntersectionObserver((entries)=>{
  for(const e of entries){ if(e.isIntersecting){ e.target.classList.add('in'); reveal.unobserve(e.target); } }
},{threshold:.12});
document.querySelectorAll('section').forEach(s=>reveal.observe(s));

const mast = document.querySelector('.mast');
if(mast) mast.animate(
  [{opacity:0,transform:'translateY(18px)'},{opacity:1,transform:'none'}],
  {duration:900,easing:'cubic-bezier(.2,.7,.3,1)',fill:'both'}
);

/* figures animate only while on screen — keeps multi-figure pages calm */
const pauser = new IntersectionObserver((entries)=>{
  for(const e of entries) e.target.classList.toggle('paused', !e.isIntersecting);
},{rootMargin:'160px 0px'});
document.querySelectorAll('.panel').forEach(p=>pauser.observe(p));

/* rail scrollspy */
const rail = document.querySelector('.rail');
if(rail){
  const links = [...rail.querySelectorAll('a[href^="#"]')];
  const targets = links.map(a=>document.getElementById(a.getAttribute('href').slice(1))).filter(Boolean);
  let queued = false;
  const spy = ()=>{
    queued = false;
    let current = targets[0];
    for(const t of targets){ if(t.getBoundingClientRect().top <= 140) current = t; }
    for(const a of links) a.classList.toggle('cur', current && a.getAttribute('href') === '#'+current.id);
  };
  addEventListener('scroll', ()=>{ if(!queued){ queued = true; requestAnimationFrame(spy); } }, {passive:true});
  spy();
}
