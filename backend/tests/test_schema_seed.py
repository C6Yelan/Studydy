from sqlalchemy import text

from scripts.seed_demo_data import DEMO_CONCEPTS, DEMO_LEARNING_PATH, DEMO_RELATIONS, seed_demo_data


def test_demo_seed_baseline_smoke(engine):
    seed_demo_data()
    seed_demo_data()

    with engine.connect() as connection:
        material_id = connection.execute(
            text(
                """
                select id
                from materials
                where title = 'Linear Structures and ADT'
                  and subject = 'data_structure'
                  and chapter_range = 'Linear Structures'
                """
            )
        ).scalar_one()
        concept_names = connection.execute(
            text("select name from concepts where name = any(:names) order by name"),
            {"names": DEMO_CONCEPTS},
        ).scalars().all()
        relation_rows = connection.execute(
            text(
                """
                select
                    source.name as source_name,
                    target.name as target_name,
                    relation.relation_type::text as relation_type,
                    count(e.id) as evidence_count
                from concept_relations relation
                join concepts source on source.id = relation.source_concept_id
                join concepts target on target.id = relation.target_concept_id
                left join evidence e on e.relation_id = relation.id
                where source.name = any(:names)
                  and target.name = any(:names)
                group by source.name, target.name, relation.relation_type
                """
            ),
            {"names": DEMO_CONCEPTS},
        ).mappings().all()
        concept_evidence_count = connection.execute(
            text(
                """
                select count(distinct c.id)
                from concepts c
                join evidence e on e.concept_id = c.id
                where c.name = any(:names)
                """
            ),
            {"names": DEMO_CONCEPTS},
        ).scalar_one()
        path_nodes = connection.execute(
            text(
                """
                select c.name
                from learning_paths path
                join learning_path_nodes node on node.learning_path_id = path.id
                join concepts c on c.id = node.concept_id
                where path.material_id = :material_id
                order by node.position
                """
            ),
            {"material_id": material_id},
        ).scalars().all()

    relation_evidence_counts = {
        (row["source_name"], row["target_name"], row["relation_type"]): row["evidence_count"]
        for row in relation_rows
    }
    assert concept_names == sorted(DEMO_CONCEPTS)
    assert set(relation_evidence_counts) == set(DEMO_RELATIONS)
    assert all(count >= 1 for count in relation_evidence_counts.values())
    assert concept_evidence_count == len(DEMO_CONCEPTS)
    assert path_nodes == DEMO_LEARNING_PATH
