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
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:url_launcher/url_launcher.dart';

class MarkdownDrawer extends StatelessWidget {
  final String markdownContent;
  final String title;

  const MarkdownDrawer({
    super.key,
    required this.markdownContent,
    this.title = 'Documentation',
  });

  @override
  Widget build(BuildContext context) {
    return Drawer(
      child: Column(
        children: [
          DrawerHeader(
            decoration: const BoxDecoration(
              color: Color(0xFF0D47A1), // Dark blue to match app theme
            ),
            child: Center(
              child: Text(
                title,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.all(16.0),
              child: MarkdownBody(
                data: markdownContent,
                styleSheet: MarkdownStyleSheet(
                  p: const TextStyle(fontSize: 16),
                  h1: const TextStyle(
                    fontSize: 24,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF0D47A1),
                  ),
                  h2: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF1976D2),
                  ),
                  h3: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF1976D2),
                  ),
                  code: const TextStyle(
                    backgroundColor: Color(0xFFE1F5FE),
                    color: Color(0xFF01579B),
                  ),
                  codeblockDecoration: BoxDecoration(
                    color: const Color(0xFFE1F5FE),
                    borderRadius: BorderRadius.circular(4.0),
                  ),
                  blockquote: TextStyle(
                    color: const Color(0xFF0D47A1).withOpacity(0.7),
                    fontStyle: FontStyle.italic,
                  ),
                  listBullet: const TextStyle(
                    color: Color(0xFF0D47A1),
                  ),
                ),
                onTapLink: (text, href, title) async {
                  // Launch the URL when a link is tapped
                  if (href != null) {
                    try {
                      final Uri url = Uri.parse(href);
                      if (await canLaunchUrl(url)) {
                        await launchUrl(url, mode: LaunchMode.externalApplication);
                      } else {
                        // Show error if URL can't be launched
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            SnackBar(content: Text('Could not launch: $href')),
                          );
                        }
                      }
                    } catch (e) {
                      // Show error if URL parsing fails
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text('Invalid URL: $href')),
                        );
                      }
                    }
                  }
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}
