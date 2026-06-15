enum MarketRegion { all, korea, unitedStates }

extension MarketRegionLabel on MarketRegion {
  String get label {
    switch (this) {
      case MarketRegion.all:
        return '전체';
      case MarketRegion.korea:
        return '한국';
      case MarketRegion.unitedStates:
        return '미국';
    }
  }

  String get compactLabel {
    switch (this) {
      case MarketRegion.all:
        return 'ALL';
      case MarketRegion.korea:
        return 'KR';
      case MarketRegion.unitedStates:
        return 'US';
    }
  }
}

enum FlowStage { build, breakout, expansion, pullback, riskOff }

extension FlowStageLabel on FlowStage {
  String get label {
    switch (this) {
      case FlowStage.build:
        return '매집';
      case FlowStage.breakout:
        return '돌파';
      case FlowStage.expansion:
        return '확산';
      case FlowStage.pullback:
        return '눌림';
      case FlowStage.riskOff:
        return '경계';
    }
  }
}

class AppUser {
  const AppUser({
    required this.id,
    required this.name,
    required this.role,
    required this.riskProfile,
    required this.watchlistCount,
  });

  final String id;
  final String name;
  final String role;
  final String riskProfile;
  final int watchlistCount;
}

class MarketPulse {
  const MarketPulse({
    required this.region,
    required this.title,
    required this.score,
    required this.change,
    required this.bias,
    required this.netFlow,
    required this.summary,
    required this.heat,
    required this.updatedLabel,
  });

  final MarketRegion region;
  final String title;
  final int score;
  final double change;
  final String bias;
  final String netFlow;
  final String summary;
  final List<double> heat;
  final String updatedLabel;
}

class ThemePulse {
  const ThemePulse({
    required this.id,
    required this.name,
    required this.region,
    required this.stage,
    required this.score,
    required this.diffusion,
    required this.momentum,
    required this.risk,
    required this.leaders,
    required this.narrative,
    required this.trend,
  });

  final String id;
  final String name;
  final MarketRegion region;
  final FlowStage stage;
  final int score;
  final int diffusion;
  final int momentum;
  final int risk;
  final List<String> leaders;
  final String narrative;
  final List<double> trend;
}

class EquityFlow {
  const EquityFlow({
    required this.symbol,
    required this.name,
    required this.region,
    required this.theme,
    required this.priceLabel,
    required this.changePercent,
    required this.flowScore,
    required this.relativeStrength,
    required this.volumeSurge,
    required this.risk,
    required this.stage,
    required this.tags,
    required this.thesis,
    required this.sparkline,
  });

  final String symbol;
  final String name;
  final MarketRegion region;
  final String theme;
  final String priceLabel;
  final double changePercent;
  final int flowScore;
  final int relativeStrength;
  final int volumeSurge;
  final int risk;
  final FlowStage stage;
  final List<String> tags;
  final String thesis;
  final List<double> sparkline;
}

class JournalEntry {
  const JournalEntry({
    required this.id,
    required this.userId,
    required this.symbol,
    required this.thesis,
    required this.emotion,
    required this.confidence,
    required this.outcome,
    required this.createdLabel,
  });

  final String id;
  final String userId;
  final String symbol;
  final String thesis;
  final String emotion;
  final int confidence;
  final String outcome;
  final String createdLabel;

  JournalEntry copyWith({
    String? id,
    String? userId,
    String? symbol,
    String? thesis,
    String? emotion,
    int? confidence,
    String? outcome,
    String? createdLabel,
  }) {
    return JournalEntry(
      id: id ?? this.id,
      userId: userId ?? this.userId,
      symbol: symbol ?? this.symbol,
      thesis: thesis ?? this.thesis,
      emotion: emotion ?? this.emotion,
      confidence: confidence ?? this.confidence,
      outcome: outcome ?? this.outcome,
      createdLabel: createdLabel ?? this.createdLabel,
    );
  }
}
