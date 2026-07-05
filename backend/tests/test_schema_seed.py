import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts.seed_demo_data import DEMO_CONCEPTS, DEMO_LEARNING_PATH, DEMO_RELATIONS, seed_demo_data


@pytest.fixture()
def seeded_database(engine):
    seed_demo_data()
    seed_demo_data()
    return engine


def test_seed_is_idempotent_and_creates_expected_demo_data(seeded_database):
    with seeded_database.connect() as connection:
        material_count = connection.execute(
            text(
                """
                select count(*)
                from materials
                where title = 'Linear Structures and ADT'
                  and subject = 'data_structure'
                  and chapter_range = 'Linear Structures'
                """
            )
        ).scalar_one()
        concept_count = connection.execute(
            text("select count(*) from concepts where name = any(:names)"),
            {"names": DEMO_CONCEPTS},
        ).scalar_one()
        relation_count = connection.execute(text("select count(*) from concept_relations")).scalar_one()
        learning_path_count = connection.execute(text("select count(*) from learning_paths")).scalar_one()

    assert material_count == 1
    assert concept_count == 7
    assert relation_count == 8
    assert learning_path_count == 1


def test_every_seeded_concept_has_evidence(seeded_database):
    with seeded_database.connect() as connection:
        rows = connection.execute(
            text(
                """
                select c.name, count(e.id) as evidence_count
                from concepts c
                left join evidence e on e.concept_id = c.id
                where c.name = any(:names)
                group by c.name
                """
            ),
            {"names": DEMO_CONCEPTS},
        ).mappings().all()

    evidence_counts = {row["name"]: row["evidence_count"] for row in rows}
    assert set(evidence_counts) == set(DEMO_CONCEPTS)
    assert all(count >= 1 for count in evidence_counts.values())


def test_seeded_relations_match_fixed_set(seeded_database):
    with seeded_database.connect() as connection:
        rows = connection.execute(
            text(
                """
                select source.name as source_name, target.name as target_name, relation_type::text as relation_type
                from concept_relations relation
                join concepts source on source.id = relation.source_concept_id
                join concepts target on target.id = relation.target_concept_id
                order by source.name, target.name, relation_type
                """
            )
        ).all()

    actual = {(row.source_name, row.target_name, row.relation_type) for row in rows}
    assert actual == set(DEMO_RELATIONS)


def test_learning_path_nodes_are_in_expected_order(seeded_database):
    with seeded_database.connect() as connection:
        rows = connection.execute(
            text(
                """
                select c.name
                from learning_path_nodes node
                join concepts c on c.id = node.concept_id
                order by node.position
                """
            )
        ).scalars().all()

    assert rows == DEMO_LEARNING_PATH


def test_relation_fk_rejects_missing_source_or_target(engine):
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into concept_relations (source_concept_id, target_concept_id, relation_type)
                    values (999001, 999002, 'prerequisite')
                    """
                )
            )


def test_relation_type_enum_rejects_invalid_value(engine):
    with engine.begin() as connection:
        source_id = connection.execute(text("insert into concepts (name) values ('enum source') returning id")).scalar_one()
        target_id = connection.execute(text("insert into concepts (name) values ('enum target') returning id")).scalar_one()

    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into concept_relations (source_concept_id, target_concept_id, relation_type)
                    values (:source_id, :target_id, 'not_allowed')
                    """
                ),
                {"source_id": source_id, "target_id": target_id},
            )


def test_needs_review_defaults_to_false(engine):
    with engine.begin() as connection:
        concept_row = connection.execute(
            text("insert into concepts (name) values ('default concept') returning id, needs_review")
        ).mappings().one()
        target_id = connection.execute(text("insert into concepts (name) values ('default target') returning id")).scalar_one()
        relation_row = connection.execute(
            text(
                """
                insert into concept_relations (source_concept_id, target_concept_id, relation_type)
                values (:source_id, :target_id, 'similar')
                returning id, needs_review
                """
            ),
            {"source_id": concept_row["id"], "target_id": target_id},
        ).mappings().one()

    assert concept_row["needs_review"] is False
    assert relation_row["needs_review"] is False


def test_score_detail_jsonb_can_be_saved_and_read(engine):
    with engine.begin() as connection:
        concept_row = connection.execute(
            text(
                """
                insert into concepts (name, score_detail)
                values ('json concept', cast(:detail as jsonb))
                returning score_detail
                """
            ),
            {"detail": '{"kind": "concept", "score": 1}'},
        ).mappings().one()
        target_id = connection.execute(text("insert into concepts (name) values ('json target') returning id")).scalar_one()
        source_id = connection.execute(text("select id from concepts where name = 'json concept'")).scalar_one()
        relation_row = connection.execute(
            text(
                """
                insert into concept_relations (source_concept_id, target_concept_id, relation_type, score_detail)
                values (:source_id, :target_id, 'example', cast(:detail as jsonb))
                returning score_detail
                """
            ),
            {"source_id": source_id, "target_id": target_id, "detail": '{"kind": "relation", "score": 2}'},
        ).mappings().one()

    assert concept_row["score_detail"] == {"kind": "concept", "score": 1}
    assert relation_row["score_detail"] == {"kind": "relation", "score": 2}
