function updateReportLinks() {
  const run = document.getElementById('history').value;
  if (!run?.startsWith('live_metric')) return;
  const query = '?run=' + encodeURIComponent(run);
  document.getElementById('summaryReport').href = '/report' + query + '&view=summary';
  document.getElementById('recordsReport').href = '/report' + query + '&view=records';
  document.getElementById('summaryCsv').href = '/api/summary-csv' + query;
}
document.getElementById('history').addEventListener('change', updateReportLinks);
setInterval(updateReportLinks, 500);
updateReportLinks();
