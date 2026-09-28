// Info-Hinweise: Ein Knopf zeigt/verbirgt eine kurze Erklärung (Disclosure-Muster).
// Klick oder Enter/Leertaste öffnet, Escape oder Klick daneben schließt. aria-expanded
// sagt Screenreadern den Zustand, aria-controls verbindet Knopf und Text.
(function () {
  function schliessen(ausser) {
    document.querySelectorAll('.info-knopf[aria-expanded="true"]').forEach(function (k) {
      if (k === ausser) return;
      k.setAttribute("aria-expanded", "false");
      document.getElementById(k.getAttribute("aria-controls")).hidden = true;
    });
  }
  document.addEventListener("click", function (e) {
    var knopf = e.target.closest(".info-knopf");
    if (!knopf) { if (!e.target.closest(".info-text")) schliessen(null); return; }
    var offen = knopf.getAttribute("aria-expanded") === "true";
    schliessen(knopf);
    knopf.setAttribute("aria-expanded", String(!offen));
    document.getElementById(knopf.getAttribute("aria-controls")).hidden = offen;
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var offen = document.querySelector('.info-knopf[aria-expanded="true"]');
    schliessen(null);
    if (offen) offen.focus();
  });
})();
