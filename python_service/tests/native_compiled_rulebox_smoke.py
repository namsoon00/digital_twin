"""Opt-in differential check: identical frozen facts, native TypeQL vs RuleBox.

Creates and deletes only a uniquely named temporary TypeDB. Never dispatches.
Accepts a frozen release artifact and a JSON source graph; see compiled runtime docs.
"""
import json
import re
import time
import uuid
from collections import defaultdict
from copy import deepcopy

from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.typedb_ontology import typedb_repository_from_settings
from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import ontology_seed_graph_from_artifact
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import relation_row_id, ontology_storage_id
from digital_twin.modules.reasoning.infrastructure.typeql import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.constants import TYPEDB_NUMERIC_ATTRIBUTES
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from digital_twin.modules.reasoning.domain.ontology_semantics import typedb_class_type
from digital_twin.modules.model_registry.contracts import CLASS_DEFS
from digital_twin.modules.reasoning.domain.compiled_rulebox import compile_rulebox, CompiledRuleboxEvaluator, fingerprint


def run(artifact, graph):
    program = compile_rulebox(artifact['rules'], tbox_fingerprint=artifact['tboxFingerprint'])
    rules = [r for r in program.rules() if r.enabled]
    database = 'orbit_compiled_parity_' + uuid.uuid4().hex[:12]
    settings = runtime_settings(fast_operational_read=True)
    repo = typedb_repository_from_settings({**settings, 'typedbDatabase': database, 'typedbRetryCount': '0'})
    imported = repo.driver_imports()
    if not imported[0]:
        raise RuntimeError('TypeDB driver unavailable')
    tt = imported[0][-1]
    node_rows, relation_rows = repo.rows_for_entities(graph), repo.rows_for_relations(graph)
    variables = {r['id']: '$n' + str(i) for i, r in enumerate(node_rows)}
    # Compare predicate semantics against the complete frozen facts. The legacy
    # physical mapper deliberately omits some attributes on context-only rows;
    # this oracle must not turn those stored JSON facts into absent values.
    from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import typedb_target_attribute
    from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_literal_for_attribute
    clauses, restored_attributes = [], []
    facts = {e.entity_id:e for e in graph.entities}
    for row in node_rows:
        clause = repo.node_insert_clause(row, '2026-11-13T00:00:00Z', variables[row['id']])
        queried_fields = {key for rule in rules for condition in rule.conditions
                          if not condition.target_kind or condition.target_kind == row['kind']
                          for key in condition.target_property_filters}
        for field in sorted(queried_fields):
            field = field[3].lower()+field[4:] if field.startswith(('min','max')) and len(field)>3 else field
            attribute = typedb_target_attribute(field)
            value = facts[row['id']].properties.get(field)
            if isinstance(value,(dict,list,tuple,set)):
                continue
            if attribute in TYPEDB_NUMERIC_ATTRIBUTES:
                from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_number
                if typedb_number(value) is None:
                    continue
            if attribute and value is not None and value != '' and ', has '+attribute+' ' not in clause:
                clause += ', has '+attribute+' '+typedb_literal_for_attribute(attribute,value)
                restored_attributes.append({'kind':row['kind'],'field':field})
        clauses.append(clause+';')
    relation_ids = defaultdict(list)
    for i, r in enumerate(relation_rows):
        rid = relation_row_id(r)
        r['storageId'] = rid
        relation_ids[r['type']].append(ontology_storage_id(r,rid,'relation'))
        clauses.append(repo.relation_insert_clause(r, '2026-11-13T00:00:00Z', '$r'+str(i), variables[r['source']], variables[r['target']]) + ';')
    # N-of-M native queries count persisted RuleBox condition tokens.
    from digital_twin.modules.reasoning.contracts import entity_id
    for i, (rule, c) in enumerate((r,c) for r in rules for c in r.conditions if c.role in {'any','optional'}):
        clauses.append('$token'+str(i)+' isa ontology-node, has ontology-id '+typedb_string(entity_id('rule-condition',rule.rule_id+':'+c.condition_id))+', has ontology-box "RuleBox";')
    plans = []
    for r in rules:
        ids = [ontology_storage_id(row,row['id'],'node') for row in node_rows if row['kind'] == r.source_kind]
        plan = typedb_native_match_query(r.to_dict(), active_source_storage_ids=ids,
            active_relation_storage_ids_by_type=relation_ids, world_id=graph.worldview.get('worldId',''), compact_result_rows=True) if ids else {}
        if ids and not plan.get('query') and not str(plan.get('reason','')).startswith('Active Manifest evidence index has no relation'):
            raise RuntimeError('Unverified native rule '+r.rule_id+': '+str(plan.get('reason')))
        plans.append((r, plan))
    # Keep the real row mapper/type hierarchy, omitting only its opaque JSON archive.
    insert = 'insert '+ ' '.join(clauses)
    insert = re.sub(r', has ontology-json "(?:\\.|[^"\\])*"', '', insert)
    syntax = re.sub(r'"(?:\\.|[^"\\])*"', '""', insert+' '.join(p.get('query','') for _,p in plans))
    attributes = set(re.findall(r'\bhas ([\w-]+)', syntax))
    classes = {typedb_class_type(d.name): typedb_class_type(d.parent) if d.parent else 'ontology-node' for d in CLASS_DEFS}
    used_types = set(re.findall(r'\bisa ([\w-]+)', syntax))
    reltypes = {t for t in used_types if t.startswith('ontology-relation-')} | {'ontology-assertion'}
    for t in used_types - reltypes - {'ontology-node'}:
        classes.setdefault(t,'ontology-node')
    for parent in list(classes.values()):
        if parent != 'ontology-node': classes.setdefault(parent,'ontology-node')
    needed = used_types - reltypes - {'ontology-node'}
    while True:
        expanded = needed | {classes[t] for t in needed if classes[t] != 'ontology-node'}
        if expanded == needed: break
        needed = expanded
    classes = {t:p for t,p in classes.items() if t in needed}
    schema = 'define '+ ' '.join('attribute '+a+', value '+('double' if a in TYPEDB_NUMERIC_ATTRIBUTES else 'string')+';' for a in sorted(attributes))
    owns=', '.join('owns '+a+(' @unique' if a=='ontology-storage-id' else '') for a in sorted(attributes))
    schema += ' entity ontology-node, '+owns+', plays ontology-assertion:source, plays ontology-assertion:target;'
    schema += ' relation ontology-assertion, relates source, relates target, '+owns+';'
    schema += ' '.join(' entity '+t+', sub '+p+';' for t,p in classes.items())
    schema += ' '.join(' relation '+t+', sub ontology-assertion;' for t in reltypes if t!='ontology-assertion')
    driver = repo.create_driver(imported)
    created = False
    native_pairs, evaluated = set(), []
    predicate_rules = []
    started = time.perf_counter()
    try:
        driver.databases.create(database); created = True
        with driver.transaction(database, tt.SCHEMA) as tx:
            tx.query(schema).resolve(); tx.commit()
        with driver.transaction(database, tt.WRITE) as tx:
            tx.query(insert).resolve(); tx.commit()
        for r, plan in plans:
            print('native parity '+r.rule_id, flush=True)
            # Wide OR branches exhaust the native planner even in a tiny DB.
            # Query each exact native predicate instead and combine source-ID
            # sets independently of the compiled value/filter implementation.
            if sum(c.role in {'any','optional'} for c in r.conditions) >= 5:
                predicate_rules.append(r.rule_id)
                source_rows = [row for row in node_rows if row['kind']==r.source_kind]
                universe = {row['id'] for row in source_rows}
                accepted = set(universe)
                any_groups = defaultdict(set)
                for c in r.conditions:
                    single = deepcopy(r.to_dict())
                    single['conditions'] = [{**c.to_dict(), 'role':'required'}]
                    single['any_condition_min_count'] = 1
                    probe = typedb_native_match_query(single,
                        active_source_storage_ids=[ontology_storage_id(row,row['id'],'node') for row in source_rows],
                        active_relation_storage_ids_by_type=relation_ids, world_id=graph.worldview.get('worldId',''), compact_result_rows=True)
                    matched = set()
                    if probe.get('query'):
                        with driver.transaction(database, tt.READ) as tx:
                            matched = {row['sourceId'] for row in repo.read_rows_in_transaction(tx,probe['query'],['sourceId'])}
                    elif not str(probe.get('reason','')).startswith('Active Manifest evidence index has no relation'):
                        raise RuntimeError('Native predicate unavailable: '+str(probe.get('reason')))
                    if c.role == 'required': accepted &= matched
                    elif c.role == 'not': accepted -= matched
                    elif c.role in {'any','optional'}:
                        for sid in matched: any_groups[sid].add(c.evidence_group_key or c.condition_id)
                accepted = {sid for sid in accepted if len(any_groups[sid]) >= r.any_condition_min_count}
                native_pairs.update((r.rule_id,sid) for sid in accepted)
                evaluated.append(r.rule_id)
                continue
            if plan.get('query'):
                with driver.transaction(database, tt.READ) as tx:
                    rows = repo.read_rows_in_transaction(tx, plan['query'], ['sourceId'])
                native_pairs.update((r.rule_id,row['sourceId']) for row in rows)
            evaluated.append(r.rule_id)
        native_ms=round((time.perf_counter()-started)*1000)
        result=CompiledRuleboxEvaluator(program).evaluate(graph, materialize=False)
        compiled_pairs={(r['ruleId'],r['sourceId']) for r in result['matches'] if not r.get('managementResolution')}
        differences={'nativeOnly':sorted(native_pairs-compiled_pairs),'compiledOnly':sorted(compiled_pairs-native_pairs)}
        report={'status':'passed' if not any(differences.values()) else 'failed', 'programFingerprint':program.fingerprint,
            'sourceFingerprint':fingerprint({'entities':[e.to_dict() for e in graph.entities],'relations':[r.to_dict() for r in graph.relations]}),
            'testedRuleIds':sorted(evaluated), 'completeRuleCoverage':set(evaluated)==set(program.rule_order),
            'nativeMatchedCount':len(native_pairs),'compiledMatchedCount':len(compiled_pairs),'differences':differences,
            'nativePredicateCompositionRuleIds':predicate_rules,
            'oracleFactAttributesRestored':restored_attributes,
            'comparisonScope':'same-frozen-facts-and-predicate-semantics; excludes-legacy-physical-omissions',
            'nativeMs':native_ms,'compiledMs':result['durationMs'],'operationalGraphMutated':False,'deliveryCount':0}
        return report
    finally:
        if created: driver.databases.get(database).delete()
        driver.close()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact',required=True)
    parser.add_argument('--graph',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    artifact=json.load(open(args.artifact))
    graph=ontology_seed_graph_from_artifact({'graph':json.load(open(args.graph))})
    result=run(artifact,graph)
    with open(args.output,'w') as f: json.dump(result,f)
    print(json.dumps(result))
    raise SystemExit(0 if result['status']=='passed' else 1)
