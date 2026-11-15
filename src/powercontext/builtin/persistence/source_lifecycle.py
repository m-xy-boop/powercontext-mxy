# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Topic eligibility predicates shared by native retrieval projections."""

from sqlalchemy import Table, select
from sqlalchemy.sql import ColumnElement

from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE


def topic_lifecycle_available(topic: Table) -> ColumnElement[bool]:
    nodes = SOURCE_LIFECYCLE_NODES_TABLE
    return (
        ~select(nodes.c.node_id)
        .where(
            nodes.c.scope_id == topic.c.scope_id,
            nodes.c.artifact_id == topic.c.artifact_id,
            nodes.c.artifact_revision == topic.c.revision,
            nodes.c.kind == "topic",
            nodes.c.available.is_(False),
        )
        .correlate(topic)
        .exists()
    )


def topic_lifecycle_sql(alias: str) -> str:
    """Build the same predicate for backend SQL using an internal static alias."""

    return f"""NOT EXISTS (
        SELECT 1 FROM pc_source_lifecycle_nodes AS lifecycle
        WHERE lifecycle.scope_id = {alias}.scope_id
          AND lifecycle.artifact_id = {alias}.artifact_id
          AND lifecycle.artifact_revision = {alias}.revision
          AND lifecycle.kind = 'topic' AND lifecycle.available = 0
    )"""  # noqa: S608 - only internal static SQL identifiers/predicates
