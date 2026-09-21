/* Navigation-only presentation helpers. Existing controls remain the source of truth. */
(() => {
  document.querySelectorAll('[data-studio-action]').forEach(button => button.addEventListener('click', () => {
    const action = button.dataset.studioAction;
    if (action === 'new') document.getElementById('projects-new-btn')?.click();
    else document.querySelector(`.nav-item[data-view="${action}"]`)?.click();
  }));
  const settings = document.getElementById('view-setup');
  if (!settings) return;
  const nav = document.createElement('nav');
  nav.className = 'studio-settings-nav'; nav.setAttribute('aria-label', 'Settings sections');
  const sections = [['Hardware','setup-hardware'],['Acceleration','gpu-accel'],['Models','ai-ollama-model'],['Dependencies','setup-deps'],['Storage','media-cache-status']];
  sections.forEach(([name,id]) => {
    const target = document.getElementById(id)?.closest('.card');
    if (!target) return;
    const button = document.createElement('button'); button.type='button';button.className='btn btn-small btn-secondary';button.textContent=name;
    button.addEventListener('click', () => { if(target.tagName==='DETAILS') target.open=true; target.scrollIntoView({block:'start',behavior:'smooth'}); });
    nav.append(button);
  });
  settings.querySelector('.view-header')?.after(nav);
})();
