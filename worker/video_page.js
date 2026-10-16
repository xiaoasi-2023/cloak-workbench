function(action, config) {
  const trusted = location.protocol === 'https:' && (location.hostname === 'dola.com' || location.hostname.endsWith('.dola.com'));
  if (!trusted) return action === 'observe' ? {trusted:false, ready:document.readyState, url:location.href} : false;
  const visible = e => e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const label = e => (e.innerText || e.getAttribute('aria-label') || e.getAttribute('title') || '').trim();
  const controls = () => [...document.querySelectorAll('button,[role="button"],[role="option"],[role="menuitem"],[role="radio"],label,span')]
    .filter(e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true');
  const clickExact = matcher => {
    const matches = controls().filter(e => matcher(label(e)));
    const leaf = matches.filter(e => !matches.some(other => other !== e && e.contains(other)));
    if (leaf.length !== 1) return false;
    leaf[0].click(); return true;
  };
  const text = (document.body?.innerText || '').slice(0, 24000);
  if (action === 'observe') {
    let problem = '';
    if (/not available in (?:your|this) (?:country|region)|この国または地域では.*利用できません|所在地区.*不可用|地区.*暂不支持/i.test(text)) problem='当前代理地区不支持 Dola，请更换可用地区。';
    else if (/insufficient (?:credits|points)|not enough (?:credits|points)|积分不足|额度不足|クレジット.*不足|ポイント.*不足/i.test(text)) problem='账号额度不足，请在 Dola 查看余额。';
    else if ([...document.querySelectorAll('iframe[src*="captcha"],iframe[src*="recaptcha"],[class*="captcha-verify"]')].some(visible) || /verify you are human|人机验证|完成安全验证/i.test(text)) problem='需要手动完成安全验证。';
    const videos = [...document.querySelectorAll('video')].filter(e => (e.currentSrc || e.src) && !e.closest('[class*="example"],[class*="template"],[class*="recommend"]'));
    const video = /^\/chat\/\d+\/?$/.test(location.pathname) && text.includes(config.prompt)
      ? videos.find(e => Number.isFinite(e.duration) && e.duration > 0) : null;
    return {trusted:true, ready:document.readyState, problem, pageUrl:location.href,
      duration:video ? video.duration : null};
  }
  if (action === 'enter') return clickExact(s => /^(Create video|Generate video|Video generation|動画を作成|動画生成|视频生成|生成视频|创作视频|制作视频)$/i.test(s));
  if (action.startsWith('choose_')) {
    const key=action.slice(7), value=String(config[key]);
    const desired = key === 'model' ? (value==='seedance_v2.5' ? /^(?:Dreamina\s*)?Seedance\s*2\.5(?:\s*Fast)?$|^モデル\s*2\.5$/i : /^(?:Dreamina\s*)?Seedance\s*2\.0(?:\s*(?:Fast|高速))?$|^モデル\s*2\.0高速$/i)
      : key === 'duration' ? new RegExp('^'+value+'\\s*(?:s|秒)$','i') : new RegExp('^'+value+'$');
    if (clickExact(s=>desired.test(s))) return true;
    const trigger=key==='model' ? /^(?:モデル|模型|Model)(?:\s|$)|Seedance/i : key==='duration' ? /^\d+\s*(?:s|秒)$|^(?:Duration|时长|長さ|時間)$/i : /^(?:Ratio|比例|比率|画面比例)$|^\d+:\d+$/i;
    clickExact(s=>trigger.test(s));
    return false;
  }
  if (action === 'fill') {
    const editors=[...document.querySelectorAll('textarea,[contenteditable="true"][role="textbox"],[contenteditable="true"]')].filter(visible);
    if (editors.length!==1) return false;
    const e=editors[0]; e.focus();
    if (e.tagName==='TEXTAREA') {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,config.prompt);
      e.dispatchEvent(new Event('input',{bubbles:true}));
    } else {
      const selection=window.getSelection(), range=document.createRange();range.selectNodeContents(e);selection.removeAllRanges();selection.addRange(range);
      document.execCommand('insertText',false,config.prompt);
      e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:config.prompt}));
    }
    return (e.value ?? e.innerText).trim()===config.prompt;
  }
  return false;
}
