import { buildEditorGraph } from './graph.js';
import type { EditorIssue, EditorWorkflow } from './model.js';

const ID = /^[A-Za-z0-9_-]+$/;
export function validateEditorWorkflow(workflow: EditorWorkflow): readonly EditorIssue[] {
  const issues: EditorIssue[] = [];
  if (!ID.test(workflow.name))
    issues.push({
      code: 'invalid_name',
      severity: 'error',
      message: 'Workflow name must contain only letters, numbers, _ and -.',
      field: 'name',
    });
  if (workflow.stages.length === 0)
    issues.push({
      code: 'no_stages',
      severity: 'error',
      message: 'A workflow needs at least one stage.',
    });
  const graph = buildEditorGraph(workflow);
  for (const stage of workflow.stages) {
    if (!ID.test(stage.id))
      issues.push({
        code: 'invalid_stage_id',
        severity: 'error',
        message: 'Stage ID must contain only letters, numbers, _ and -.',
        stageKey: stage.key,
        field: 'id',
      });
    if (stage.harness === null || stage.harness === '')
      issues.push({
        code: 'missing_harness',
        severity: 'error',
        message: 'Stage harness is required.',
        stageKey: stage.key,
        field: 'harness',
      });
    if (stage.model === null || stage.model === '')
      issues.push({
        code: 'missing_model',
        severity: 'error',
        message: 'Stage model is required.',
        stageKey: stage.key,
        field: 'model',
      });
    if (stage.gate !== 'auto')
      issues.push({
        code: 'unsupported_gate',
        severity: 'warning',
        message: `Gate “${stage.gate}” is not runnable yet.`,
        stageKey: stage.key,
        field: 'gate',
      });
    const seen = new Set<string>();
    stage.dependsOn.forEach((dependency, dependencyIndex) => {
      if (dependency === stage.id)
        issues.push({
          code: 'self_dependency',
          severity: 'error',
          message: 'A stage cannot depend on itself.',
          stageKey: stage.key,
          dependencyIndex,
        });
      if (seen.has(dependency))
        issues.push({
          code: 'duplicate_dependency',
          severity: 'error',
          message: `Duplicate dependency “${dependency}”.`,
          stageKey: stage.key,
          dependencyIndex,
        });
      seen.add(dependency);
    });
  }
  if (workflow.mode !== 'static')
    issues.push({
      code: 'unsupported_mode',
      severity: 'warning',
      message: `Mode “${workflow.mode}” is not runnable yet.`,
      field: 'mode',
    });
  for (const nodeIssues of graph.issuesByNode.values()) issues.push(...nodeIssues);
  if (workflow.stages.length > 0 && workflow.stages.every((stage) => stage.dependsOn.length > 0))
    issues.push({ code: 'no_root', severity: 'error', message: 'Workflow has no root stage.' });
  return issues;
}
