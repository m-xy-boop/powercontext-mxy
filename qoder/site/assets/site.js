/* 渐进增强：截图灯箱、命令复制、导航高亮。禁用脚本时页面内容仍完整可读。 */
(function () {
  "use strict";

  var box = document.getElementById("lightbox");
  var lbImg = document.getElementById("lb-img");
  var lbCaption = document.getElementById("lb-caption");

  function closeLightbox() {
    if (!box || box.hidden) return;
    box.hidden = true;
    lbImg.removeAttribute("src");
    lbCaption.textContent = "";
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  var lastFocus = null;

  function openLightbox(src, alt, caption) {
    if (!box) return;
    lastFocus = document.activeElement;
    lbImg.src = src;
    lbImg.alt = alt || "";
    lbCaption.textContent = (caption || "") + "（可上下滚动查看完整截图，Esc 关闭）";
    box.hidden = false;
    box.scrollTop = 0;
    var close = box.querySelector(".lb-close");
    if (close) close.focus();
  }

  document.querySelectorAll(".shot img").forEach(function (img) {
    img.addEventListener("click", function () {
      var figure = img.closest("figure");
      var caption = figure ? figure.querySelector("figcaption") : null;
      openLightbox(img.currentSrc || img.src, img.alt, caption ? caption.textContent : "");
    });
  });

  if (box) {
    box.addEventListener("click", function (event) {
      if (event.target === box || event.target.classList.contains("lb-close")) closeLightbox();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeLightbox();
    });
  }

  var copy = document.querySelector(".copy[data-copy]");
  if (copy) {
    copy.addEventListener("click", function () {
      var target = document.getElementById(copy.dataset.copy);
      if (!target) return;
      var text = target.textContent;
      var done = function () {
        copy.textContent = "已复制";
        setTimeout(function () { copy.textContent = "复制"; }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallback(text, done); });
      } else {
        fallback(text, done);
      }
    });
  }

  function fallback(text, done) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      done();
    } catch (error) {
      copy.textContent = "请手动复制";
    }
    document.body.removeChild(area);
  }

  var links = Array.prototype.slice.call(document.querySelectorAll(".nav a"));
  var sections = links
    .map(function (link) { return document.querySelector(link.getAttribute("href")); })
    .filter(Boolean);

  if ("IntersectionObserver" in window && sections.length) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        links.forEach(function (link) {
          var active = link.getAttribute("href") === "#" + entry.target.id;
          if (active) { link.setAttribute("aria-current", "true"); } else { link.removeAttribute("aria-current"); }
        });
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    sections.forEach(function (section) { observer.observe(section); });
  }
})();
