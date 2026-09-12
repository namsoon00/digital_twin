function groupTodayTasks(tasks) {
  var groups = {investment: [], pending: [], calendar: [], operations: []};
  (tasks || []).forEach(function (task) {
    if (task.kind === "판단") {
      var pending = task.reading && ["awaiting", "unavailable"].includes(task.reading.kind);
      groups[pending ? "pending" : "investment"].push(task);
    } else groups[task.kind === "일정" ? "calendar" : "operations"].push(task);
  });
  return groups;
}

export { groupTodayTasks };
