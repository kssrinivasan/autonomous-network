// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import 'package:flutter/material.dart';
import 'package:networkagent/appstate.dart';
import 'package:provider/provider.dart';
import 'screens/network_dashboard.dart';

void main() async {
  runApp(
    ChangeNotifierProvider(
      create: (context) => Appstate(),
      child: MaterialApp(
        home: NetworkAgentApp()
      )
    )
  );
}

class NetworkAgentApp extends StatelessWidget {
  const NetworkAgentApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Digital Twin Dashboard',
      theme: ThemeData(
        colorScheme: ColorScheme.light(
          primary: const Color(0xFF0D47A1), // Dark blue
          secondary: const Color(0xFF1976D2), // Lighter blue
          surface: Colors.white,
          background: Colors.white,
          onPrimary: Colors.white,
        ),
        scaffoldBackgroundColor: Colors.white,
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFF0D47A1),
          foregroundColor: Colors.white,
        ),
        cardTheme: CardThemeData(
          color: Colors.white,
          elevation: 2,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
          ),
        ),
        dividerTheme: const DividerThemeData(
          color: Color(0xFFE0E0E0),
        ),
        useMaterial3: true,
      ),
      // No login screen: the service is deployed behind IAP, so the request
      // that served this bundle was already authenticated and the backend
      // receives the IAP-asserted identity.
      home: const NetworkDashboard(),
    );
  }
}
