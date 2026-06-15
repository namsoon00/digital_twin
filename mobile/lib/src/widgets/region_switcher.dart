import 'package:flutter/material.dart';

import '../models/market_models.dart';

class RegionSwitcher extends StatelessWidget {
  const RegionSwitcher({
    required this.value,
    required this.onChanged,
    super.key,
  });

  final MarketRegion value;
  final ValueChanged<MarketRegion> onChanged;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = constraints.maxWidth < 360;
        return SegmentedButton<MarketRegion>(
          showSelectedIcon: false,
          segments: [
            for (final region in MarketRegion.values)
              ButtonSegment<MarketRegion>(
                value: region,
                label: Text(compact ? region.compactLabel : region.label),
              ),
          ],
          selected: {value},
          onSelectionChanged: (selected) => onChanged(selected.first),
        );
      },
    );
  }
}
