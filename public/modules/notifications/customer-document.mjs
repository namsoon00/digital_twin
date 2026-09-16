import { escapeHtml } from "../shared/text.mjs";

function notificationCustomerDocument(job) {
  return job && job.customerInvestmentDocument && typeof job.customerInvestmentDocument === "object"
    ? job.customerInvestmentDocument
    : {};
}

function renderNotificationCustomerDocument(job, compact) {
  var customerDocument = notificationCustomerDocument(job);
  if (!Object.keys(customerDocument).length) return "";
  var customerSections = Array.isArray(customerDocument.sections) ? customerDocument.sections : [];
  var customerLinks = Array.isArray(customerDocument.links) ? customerDocument.links : [];
  if (compact) {
    var compactKeys = customerDocument.role === "typedb-observation"
      ? ["change", "importance", "financial-evidence", "tracking", "next-update"]
      : ["change", "action", "reasons", "financial-evidence", "counter", "tracking", "next-update"];
    customerSections = customerSections.filter(function (section) {
      return compactKeys.indexOf(String((section || {}).key || "")) >= 0;
    }).slice(0, compactKeys.length);
  }
  return [
    '<section class="notification-detail-section primary notification-customer-document">',
    '<div class="notification-reasoning-head"><div><strong>' + escapeHtml(customerDocument.headline || "투자 인사이트") + '</strong><span>' + escapeHtml(customerDocument.roleLabel || "") + '</span></div></div>',
    customerDocument.lead ? '<div class="notification-detail-reasons"><p><b>한눈에 보기</b> ' + escapeHtml(customerDocument.lead) + '</p></div>' : '',
    customerSections.map(function (section) {
      var rows = Array.isArray(section.rows) ? section.rows : [];
      if (!rows.length) return "";
      if (compact) rows = rows.slice(0, section.key === "financial-evidence" ? 4 : 2);
      return '<div class="notification-detail-reasons"><strong>' + escapeHtml(section.title || "상세") + '</strong>'
        + rows.map(function (row) { return '<p>' + escapeHtml(row) + '</p>'; }).join("")
        + '</div>';
    }).join(""),
    customerLinks.length ? '<div class="notification-detail-reasons"><strong>원문</strong>'
      + customerLinks.map(function (link) {
        var url = String((link || {}).url || "");
        if (!/^https?:\/\//i.test(url)) return "";
        return '<p><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml((link || {}).label || "원문 보기") + '</a></p>';
      }).join("")
      + '</div>' : '',
    '</section>'
  ].join("");
}

export { notificationCustomerDocument, renderNotificationCustomerDocument };
