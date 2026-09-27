function groupTodayTasks(tasks) {
  var groups = {investment: [], actionable: [], observation: [], pending: [], calendar: [], operations: []};
  (tasks || []).forEach(function (task) {
    if (task.kind === "판단") {
      var pending = task.reading && ["awaiting", "unavailable"].includes(task.reading.kind);
      if (pending) {
        groups.pending.push(task);
        return;
      }
      groups.investment.push(task);
      if (todayTaskNeedsAction(task)) groups.actionable.push(task);
      else groups.observation.push(task);
    } else groups[task.kind === "일정" ? "calendar" : "operations"].push(task);
  });
  return groups;
}

function todayTaskNeedsAction(task) {
  if (!task || task.kind !== "판단") return false;
  var readingKind = String(((task || {}).reading || {}).kind || "").toLowerCase();
  var action = String(task.actionCode || "").trim().toUpperCase();
  if (readingKind && readingKind !== "opinion") return false;
  if (["NO_ACTION", "ABSTAIN", "ABSTAINED", "WATCH", "HOLD", ""].includes(action)) {
    return readingKind === "opinion" && !action;
  }
  return ["BUY", "ADD", "TRIM", "REDUCE", "SELL", "AVOID", "REBALANCE"].includes(action);
}

export { groupTodayTasks, todayTaskNeedsAction };
