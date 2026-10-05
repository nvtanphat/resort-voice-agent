/* Fixed SOS button. Kept out of index.html: the CSP (script-src 'self') blocks inline scripts. */
'use strict';
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('fixed-sos')?.addEventListener('click',()=>{
    window.dispatchEvent(new Event('concierge:sos'));
  });
});
