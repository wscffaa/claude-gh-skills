#!/usr/bin/env python3
"""Dependency scheduling utilities for parallel-agent skill."""
from __future__ import annotations

from typing import Dict, List

from task_parser import TaskSpec


class DAGSchedulerError(ValueError):
    pass


def topological_layers(tasks: List[TaskSpec]) -> List[List[TaskSpec]]:
    id_to_task: Dict[str, TaskSpec] = {task.task_id: task for task in tasks}
    indegree: Dict[str, int] = {task.task_id: 0 for task in tasks}
    adj: Dict[str, List[str]] = {}

    for task in tasks:
        for dep in task.dependencies:
            if dep not in id_to_task:
                raise DAGSchedulerError(f"dependency {dep!r} not found for task {task.task_id!r}")
            indegree[task.task_id] += 1
            adj.setdefault(dep, []).append(task.task_id)

    queue = [task.task_id for task in tasks if indegree[task.task_id] == 0]

    layers: List[List[TaskSpec]] = []
    processed = 0

    while queue:
        current = queue
        queue = []
        layer = [id_to_task[task_id] for task_id in current]
        layers.append(layer)
        processed += len(layer)

        next_ids: List[str] = []
        for task_id in current:
            for neighbor in adj.get(task_id, []):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    next_ids.append(neighbor)
        queue = next_ids

    if processed != len(tasks):
        cycle_ids = sorted([task_id for task_id, deg in indegree.items() if deg > 0])
        raise DAGSchedulerError(f"cycle detected involving tasks: {', '.join(cycle_ids)}")

    return layers
