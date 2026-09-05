/*
 * 极简前端密码门禁
 * ---------------------------------------------------------------------------
 * 首次打开站点需输入访问密码，验证通过后写入 cookie，之后重复访问免输入。
 *
 * 说明（重要）：这是纯前端方案，没有后端校验。它能挡住普通访客，
 * 但懂技术的人查看源码即可看到页面内容。仅适合内部分享用途。
 *
 * 修改密码：把 EXPECTED_HASH 换成新密码的 SHA-256 十六进制值。
 * 终端可用：  echo -n '你的密码' | shasum -a 256
 */
(function () {
  "use strict";

  // 访问密码的 SHA-256（十六进制小写）。占位符，待填入真实哈希。
  var EXPECTED_HASH = "d4e5b97a069d6a288c737f85764aab5482f824496aa9b3796ba599e782d49118";

  var COOKIE_NAME = "zyf_gate";
  var COOKIE_MAX_AGE = 60 * 60 * 24 * 365; // 1 年

  function getCookie(name) {
    var prefix = name + "=";
    var parts = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].indexOf(prefix) === 0) {
        return decodeURIComponent(parts[i].slice(prefix.length));
      }
    }
    return null;
  }

  function setCookie(name, value) {
    document.cookie =
      name +
      "=" +
      encodeURIComponent(value) +
      "; path=/; max-age=" +
      COOKIE_MAX_AGE +
      "; samesite=lax";
  }

  async function sha256Hex(text) {
    var data = new TextEncoder().encode(text);
    var buf = await crypto.subtle.digest("SHA-256", data);
    return Array.prototype.map
      .call(new Uint8Array(buf), function (b) {
        return b.toString(16).padStart(2, "0");
      })
      .join("");
  }

  // 已验证过：cookie 里存的就是正确哈希
  if (getCookie(COOKIE_NAME) === EXPECTED_HASH) {
    return;
  }

  // 在内容渲染前先把页面藏起来，避免闪现
  var hideStyle = document.createElement("style");
  hideStyle.textContent = "body{visibility:hidden!important}#zyf-gate{visibility:visible!important}";
  (document.head || document.documentElement).appendChild(hideStyle);

  function buildOverlay() {
    var overlay = document.createElement("div");
    overlay.id = "zyf-gate";
    overlay.innerHTML = [
      '<div class="zyf-gate-card">',
      '  <div class="zyf-gate-title">智遇未来 · 内部分享</div>',
      '  <div class="zyf-gate-sub">请输入访问密码</div>',
      '  <form id="zyf-gate-form">',
      '    <input id="zyf-gate-input" type="password" autocomplete="current-password" placeholder="访问密码" autofocus>',
      '    <button type="submit">进入</button>',
      '    <div id="zyf-gate-err" class="zyf-gate-err"></div>',
      '  </form>',
      "</div>",
    ].join("");

    var style = document.createElement("style");
    style.textContent = [
      "#zyf-gate{position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;justify-content:center;",
      "background:#0e1116;color:#e6e9ef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC',sans-serif}",
      "#zyf-gate .zyf-gate-card{width:min(360px,86vw);padding:32px 28px;background:#161b22;border:1px solid #232a35;border-radius:16px;text-align:center}",
      "#zyf-gate .zyf-gate-title{font-size:18px;font-weight:700;letter-spacing:.5px}",
      "#zyf-gate .zyf-gate-sub{margin-top:6px;margin-bottom:22px;font-size:13px;color:#8b94a7}",
      "#zyf-gate input{width:100%;padding:12px 14px;font-size:15px;border-radius:10px;border:1px solid #2a323e;background:#0e1116;color:#e6e9ef;outline:none}",
      "#zyf-gate input:focus{border-color:#3b82f6}",
      "#zyf-gate button{width:100%;margin-top:12px;padding:12px;font-size:15px;font-weight:600;border:none;border-radius:10px;background:#3b82f6;color:#fff;cursor:pointer;transition:background .15s}",
      "#zyf-gate button:hover{background:#2f6fd6}",
      "#zyf-gate .zyf-gate-err{min-height:18px;margin-top:12px;font-size:13px;color:#f87171}",
    ].join("");

    document.head.appendChild(style);
    document.body.appendChild(overlay);

    var form = overlay.querySelector("#zyf-gate-form");
    var input = overlay.querySelector("#zyf-gate-input");
    var err = overlay.querySelector("#zyf-gate-err");

    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      err.textContent = "";
      var hash;
      try {
        hash = await sha256Hex(input.value);
      } catch (_) {
        err.textContent = "当前环境不支持校验（需 HTTPS）";
        return;
      }
      if (hash === EXPECTED_HASH) {
        setCookie(COOKIE_NAME, EXPECTED_HASH);
        overlay.remove();
        hideStyle.remove();
      } else {
        err.textContent = "密码错误";
        input.value = "";
        input.focus();
      }
    });

    input.focus();
  }

  if (document.body) {
    buildOverlay();
  } else {
    document.addEventListener("DOMContentLoaded", buildOverlay);
  }
})();
