function flowLensPath(options, watchlistSymbols, locationSearch) {
  options = options || {};
  var params = new URLSearchParams();
  var detail = String(options.detail || "summary");
  params.set("detail", detail);
  var pageParams = new URLSearchParams(locationSearch || "");
  var mockMode = String(pageParams.get("mock") || "").toLowerCase();
  if (["1", "true", "mock"].indexOf(mockMode) >= 0) params.set("mock", "1");
  if (detail === "full" && Array.isArray(watchlistSymbols) && watchlistSymbols.length) {
    params.set("watchlistSymbols", watchlistSymbols.join(","));
  }
  if (options.refresh) params.set("refresh", "1");
  var query = params.toString();
  return "/api/flow-lens" + (query ? "?" + query : "");
}

export { flowLensPath };
