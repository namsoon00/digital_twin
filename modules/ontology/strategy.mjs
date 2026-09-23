import { renderStrategyModelingPage } from "../decisions/workspace.mjs";
import { normalizeStrategySection } from "../navigation/routes.mjs";
import { ontologyAboxEntities, ontologyAboxRelations, ontologyEntityLabelMap, ontologyIsTboxItem, ontologyRelationCounts, ontologyTypeOf } from "./graphs.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { ontologyState } from "../state/ontology.mjs";

function renderOntologyPage(snapshot) {
  decisionsState.activeStrategySection = normalizeStrategySection(decisionsState.activeStrategySection || ontologyState.activeOntologySection);
  return renderStrategyModelingPage(snapshot);
}

function ontologyStrategyParts(snapshot) {
  var decision = (snapshot || {}).tossDecision || {};
  var strategy = decision.ontologyStrategy || {};
  var tbox = strategy.tbox || {};
  var abox = strategy.abox || {};
  var rawEntities = Array.isArray(strategy.entities) ? strategy.entities : [];
  var rawRelations = Array.isArray(strategy.relations) ? strategy.relations : [];
  var tboxEntities = Array.isArray(strategy.tboxEntities) ? strategy.tboxEntities : rawEntities.filter(ontologyIsTboxItem);
  var tboxRelations = Array.isArray(strategy.tboxRelations) ? strategy.tboxRelations : rawRelations.filter(ontologyIsTboxItem);
  var aboxEntities = Array.isArray(strategy.aboxEntities) ? strategy.aboxEntities : ontologyAboxEntities(rawEntities);
  var aboxRelations = Array.isArray(strategy.aboxRelations) ? strategy.aboxRelations : ontologyAboxRelations(rawRelations);
  function mergeRows(rows, extras, keyFn) {
    var merged = [];
    var seen = {};
    (rows || []).concat(extras || []).forEach(function (row) {
      var key = keyFn(row);
      if (!key || seen[key]) return;
      seen[key] = true;
      merged.push(row);
    });
    return merged;
  }
  var entities = mergeRows(rawEntities, tboxEntities.concat(aboxEntities), function (item) { return item && item.id; });
  var relations = mergeRows(rawRelations, tboxRelations.concat(aboxRelations), function (item) {
    return item ? [item.source, ontologyTypeOf(item), item.target].join("|") : "";
  });
  var evidence = Array.isArray(strategy.evidence) ? strategy.evidence : [];
  var beliefs = Array.isArray(strategy.beliefs) ? strategy.beliefs : [];
  var opinions = Array.isArray(strategy.opinions) ? strategy.opinions : [];
  var entityLabels = ontologyEntityLabelMap(entities.concat(aboxEntities));
  return {
    decision: decision,
    investmentAnalysis: decision.investmentAnalysis || {},
    strategy: strategy,
    worldview: strategy.worldview || {},
    tbox: tbox,
    abox: abox,
    entities: entities,
    relations: relations,
    tboxEntities: tboxEntities,
    tboxRelations: tboxRelations,
    evidence: evidence,
    beliefs: beliefs,
    opinions: opinions,
    aboxEntities: aboxEntities,
    aboxRelations: aboxRelations,
    activeInvestmentOpinions: Array.isArray(strategy.activeInvestmentOpinions) ? strategy.activeInvestmentOpinions : [],
    executionPlans: Array.isArray(strategy.executionPlans) ? strategy.executionPlans : [],
    insights: Array.isArray(strategy.insights) ? strategy.insights : [],
    operationalOntology: strategy.operationalOntology || (strategy.worldview || {}).operationalOntology || {},
    dataQuality: Array.isArray(strategy.dataQuality) ? strategy.dataQuality : [],
    relationCounts: ontologyRelationCounts(aboxRelations),
    entityLabels: entityLabels
  };
}

export { ontologyStrategyParts };
