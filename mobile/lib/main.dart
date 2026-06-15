import 'package:flutter/material.dart';

import 'src/data/flow_repository.dart';
import 'src/screens/app_shell.dart';
import 'src/theme/app_theme.dart';

void main() {
  runApp(const MarketFlowApp());
}

class MarketFlowApp extends StatelessWidget {
  const MarketFlowApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'MarketFlow',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      home: AppShell(repository: MockFlowRepository()),
    );
  }
}
