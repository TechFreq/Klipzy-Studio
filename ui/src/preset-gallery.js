/* Visual choices backed by the existing caption presets and controls. */
(function () {
  const select = document.getElementById('generated-caption-preset');
  const gallery = document.getElementById('caption-preset-gallery');
  const search = document.getElementById('caption-preset-search');
  if (!select || !gallery || !search) return;
  function render() {
    gallery.replaceChildren();
    const query = search.value.trim().toLowerCase();
    Array.from(select.options).forEach(option => {
      if (!option.textContent.toLowerCase().includes(query)) return;
      const preview = CAPTION_PREVIEW[option.value] || CAPTION_PREVIEW.viral_yellow;
      const button = document.createElement('button'); button.type = 'button'; button.className = 'preset-tile';
      button.setAttribute('aria-pressed', String(option.value === select.value));
      button.dataset.preset = option.value;
      const sample = document.createElement('span'); sample.className = 'preset-sample';
      sample.style.fontFamily = preview.font; sample.style.color = preview.text;
      sample.append(document.createTextNode('Your '));
      const word = document.createElement('strong'); word.textContent = 'story'; word.style.color = preview.accent; sample.append(word);
      const name = document.createElement('span'); name.textContent = option.textContent;
      button.append(sample, name);
      button.addEventListener('click', () => {
        select.value = option.value;
        // Apply the look's colors too, so old custom overrides cannot mask it.
        const values = {'caption-primary-color':preview.text, 'caption-highlight-color':preview.accent, 'caption-outline-color':preview.back};
        Object.entries(values).forEach(([id,value]) => { const input=document.getElementById(id); if(input) input.value=value; });
        const font=document.getElementById('caption-font-name');
        const fontName=preview.font.split(',')[0].trim();
        if(font && Array.from(font.options).some(item=>item.value===fontName)) font.value=fontName;
        select.dispatchEvent(new Event('change',{bubbles:true})); render();
      });gallery.append(button);
    });
    if(!gallery.children.length) gallery.textContent='No matching caption looks.';
  }
  search.addEventListener('input',render); select.addEventListener('change',render);
  new MutationObserver(render).observe(select,{childList:true}); render();
})();
