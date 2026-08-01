"""Focused tests for Context Assembler 2 frontend framework adapters."""

from __future__ import annotations

from murder.context_compiler.extraction import (
    REL_RENDERS_COMPONENT,
    REL_STYLE_OF,
    REL_TEMPLATE_OF,
    RESOURCE_STYLE,
    RESOURCE_TEMPLATE,
    default_registry,
    reset_default_registry,
)


def setup_function() -> None:
    reset_default_registry()


def test_react_multi_component_file_roles_and_renders() -> None:
    source = """\
import { Component } from "react";

export function Profile() {
  return (
    <div>
      <ProfileCard user={user} />
      <span>plain</span>
    </div>
  );
}

function ProfileCard({ user }) {
  return <article>{user.name}</article>;
}

export function useUser(id) {
  return null;
}

function helper() {
  return 1;
}

class Widget extends Component {
  render() {
    return <ProfileCard user={{ name: "x" }} />;
  }
}
"""
    registry = default_registry()
    pipe = registry.select("src/Profile.tsx", source=source)
    assert pipe is not None
    assert pipe.base.extractor_id == "tree-sitter-typescript"
    assert [e.enricher_id for e in pipe.enrichers] == ["react"]
    assert pipe.extractor_version == "schema-1:tree-sitter-typescript-1:react-1"

    result = pipe.extract("src/Profile.tsx", source)
    roles = {
        u.unqualified_name: u.semantic_role
        for u in result.semantic_units
        if u.language_kind in {"function", "class"}
    }
    assert roles["Profile"] == "component"
    assert roles["ProfileCard"] == "component"
    assert roles["Widget"] == "component"
    assert roles["useUser"] == "hook"
    assert roles.get("helper") is None

    renders = [r for r in result.relationships if r.relation_kind == REL_RENDERS_COMPONENT]
    assert any(
        r.source_unit_local_id
        and "Profile" in (r.source_unit_local_id)
        and r.target_qualified_name == "ProfileCard"
        for r in renders
    )
    # No semantic units invented for HTML elements.
    assert not any(u.unqualified_name == "div" for u in result.semantic_units)
    assert not any(u.unqualified_name == "span" for u in result.semantic_units)


def test_vue_one_file_scoped_component_aggregate() -> None:
    source = """\
<script setup lang="ts">
import ChildCard from "./ChildCard.vue";
const label = "hi";
function greet() {
  return label;
}
</script>

<template>
  <section>
    <ChildCard />
    <div>plain</div>
  </section>
</template>

<style scoped>
section { color: red; }
</style>
"""
    registry = default_registry()
    pipe = registry.select("src/components/ProfileCard.vue", source=source)
    assert pipe is not None
    assert pipe.base.extractor_id == "vue-sfc"
    assert pipe.extractor_version == "schema-1:vue-sfc-1"
    assert pipe.language == "vue"

    result = pipe.extract("src/components/ProfileCard.vue", source)
    file_units = [u for u in result.semantic_units if u.language_kind == "file"]
    assert len(file_units) == 1
    assert file_units[0].semantic_role == "component"
    assert file_units[0].unqualified_name == "ProfileCard"

    # Script children nest under the aggregate; template/style are not components.
    assert all(
        u.parent_local_id == file_units[0].local_id
        for u in result.semantic_units
        if u.local_id != file_units[0].local_id
    )
    assert not any(
        u.semantic_role == "component" and u.language_kind != "file" for u in result.semantic_units
    )

    renders = [r for r in result.relationships if r.relation_kind == REL_RENDERS_COMPONENT]
    assert any(r.target_qualified_name == "ChildCard" for r in renders)
    assert any(r.resource_kind == RESOURCE_TEMPLATE for r in result.resource_links)
    assert any(r.resource_kind == RESOURCE_STYLE for r in result.resource_links)
    assert any(r.relation_kind == REL_STYLE_OF for r in result.relationships)


def test_svelte_one_file_scoped_component_aggregate() -> None:
    source = """\
<script lang="ts">
  import Badge from "./Badge.svelte";
  let { name } = $props();
  export function format(n: string) {
    return n.trim();
  }
</script>

<div>
  <Badge />
  {#snippet footer()}
    <p>done</p>
  {/snippet}
  {@render footer()}
</div>

<style>
  div { padding: 4px; }
</style>
"""
    registry = default_registry()
    pipe = registry.select("src/Widget.svelte", source=source)
    assert pipe is not None
    assert pipe.base.extractor_id == "svelte-sfc"
    assert pipe.extractor_version == "schema-1:svelte-sfc-1"

    result = pipe.extract("src/Widget.svelte", source)
    file_units = [u for u in result.semantic_units if u.language_kind == "file"]
    assert len(file_units) == 1
    assert file_units[0].semantic_role == "component"

    assert any(u.language_kind == "snippet" for u in result.semantic_units)
    assert any(
        r.relation_kind == REL_RENDERS_COMPONENT and r.target_qualified_name == "Badge"
        for r in result.relationships
    )
    assert any(r.resource_kind == RESOURCE_STYLE for r in result.resource_links)


def test_angular_resource_links_and_roles() -> None:
    source = """\
import { Component, Injectable } from "@angular/core";

@Component({
  selector: "app-profile",
  standalone: true,
  templateUrl: "./profile.component.html",
  styleUrls: ["./profile.component.css", "./profile.theme.css"],
  imports: [CommonModule],
  template: `<app-badge></app-badge>`,
})
export class ProfileComponent {
  title = "profile";
}

@Injectable({ providedIn: "root" })
export class ProfileService {
  load() {
    return null;
  }
}
"""
    registry = default_registry()
    pipe = registry.select("src/app/profile.component.ts", source=source)
    assert pipe is not None
    assert pipe.base.extractor_id == "tree-sitter-typescript"
    assert "angular" in [e.enricher_id for e in pipe.enrichers]
    assert "angular-1" in pipe.extractor_version

    result = pipe.extract("src/app/profile.component.ts", source)
    by_name = {u.unqualified_name: u for u in result.semantic_units}
    assert by_name["ProfileComponent"].semantic_role == "component"
    assert by_name["ProfileComponent"].metadata.get("selector") == "app-profile"
    assert by_name["ProfileService"].semantic_role == "service"

    templates = [r for r in result.resource_links if r.resource_kind == RESOURCE_TEMPLATE]
    styles = [r for r in result.resource_links if r.resource_kind == RESOURCE_STYLE]
    assert any(r.target_path.endswith("profile.component.html") for r in templates)
    assert len(styles) >= 2
    assert any(r.relation_kind == REL_TEMPLATE_OF for r in result.relationships)
    assert any(r.relation_kind == REL_STYLE_OF for r in result.relationships)


def test_registry_selects_vue_and_svelte_before_js() -> None:
    registry = default_registry()
    assert registry.resolve_language("a.vue") == "vue"
    assert registry.resolve_language("a.svelte") == "svelte"
    vue = registry.select("a.vue")
    svelte = registry.select("a.svelte")
    assert vue is not None and vue.base.extractor_id == "vue-sfc"
    assert svelte is not None and svelte.base.extractor_id == "svelte-sfc"
