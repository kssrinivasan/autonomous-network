# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

logger = logging.getLogger(__name__)


##########################################################
# Digest labels
##########################################################

def get_boolean_label(meta, label_name):
  """ Return a boolean value from based on the label string
      representation
  """
  ret = False
  if 'labels' in meta:
    if label_name in meta['labels']:
      # if label_name if matches 'true' 
      if meta['labels'][label_name].lower == 'true':
        ret = True
  return ret