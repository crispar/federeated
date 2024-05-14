# Copyright 2018, The TensorFlow Federated Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from absl.testing import absltest
from absl.testing import parameterized
import numpy as np

from tensorflow_federated.python.core.environments.tensorflow_backend import tensorflow_tree_transformations
from tensorflow_federated.python.core.impl.compiler import building_blocks
from tensorflow_federated.python.core.impl.compiler import intrinsic_defs
from tensorflow_federated.python.core.impl.compiler import tree_analysis
from tensorflow_federated.python.core.impl.types import computation_types
from tensorflow_federated.python.core.impl.types import placements
from tensorflow_federated.python.core.impl.types import type_test_utils


def _count_intrinsics(comp, uri):
  def _predicate(comp):
    return (
        isinstance(comp, building_blocks.Intrinsic)
        and uri is not None
        and comp.uri == uri
    )

  return tree_analysis.count(comp, _predicate)


class ReplaceIntrinsicsWithBodiesTest(parameterized.TestCase):

  def test_raises_on_none(self):
    with self.assertRaises(TypeError):
      tensorflow_tree_transformations.replace_intrinsics_with_bodies(None)

  def test_federated_mean_reduces_to_aggregate(self):
    uri = intrinsic_defs.FEDERATED_MEAN.uri

    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            computation_types.FederatedType(np.float32, placements.CLIENTS),
            computation_types.FederatedType(np.float32, placements.SERVER),
        ),
    )

    count_means_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_means_after_reduction = _count_intrinsics(reduced, uri)
    count_aggregations = _count_intrinsics(
        reduced, intrinsic_defs.FEDERATED_AGGREGATE.uri
    )
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_means_before_reduction, 0)
    self.assertEqual(count_means_after_reduction, 0)
    self.assertGreater(count_aggregations, 0)

  def test_federated_weighted_mean_reduces_to_aggregate(self):
    uri = intrinsic_defs.FEDERATED_WEIGHTED_MEAN.uri

    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            (computation_types.FederatedType(np.float32, placements.CLIENTS),)
            * 2,
            computation_types.FederatedType(np.float32, placements.SERVER),
        ),
    )

    count_means_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_aggregations = _count_intrinsics(
        reduced, intrinsic_defs.FEDERATED_AGGREGATE.uri
    )
    count_means_after_reduction = _count_intrinsics(reduced, uri)
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_means_before_reduction, 0)
    self.assertEqual(count_means_after_reduction, 0)
    self.assertGreater(count_aggregations, 0)

  def test_federated_min_reduces_to_aggregate(self):
    uri = intrinsic_defs.FEDERATED_MIN.uri

    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            computation_types.FederatedType(np.float32, placements.CLIENTS),
            computation_types.FederatedType(np.float32, placements.SERVER),
        ),
    )

    count_min_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_min_after_reduction = _count_intrinsics(reduced, uri)
    count_aggregations = _count_intrinsics(
        reduced, intrinsic_defs.FEDERATED_AGGREGATE.uri
    )
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_min_before_reduction, 0)
    self.assertEqual(count_min_after_reduction, 0)
    self.assertGreater(count_aggregations, 0)

  def test_federated_max_reduces_to_aggregate(self):
    uri = intrinsic_defs.FEDERATED_MAX.uri

    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            computation_types.FederatedType(np.float32, placements.CLIENTS),
            computation_types.FederatedType(np.float32, placements.SERVER),
        ),
    )

    count_max_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_max_after_reduction = _count_intrinsics(reduced, uri)
    count_aggregations = _count_intrinsics(
        reduced, intrinsic_defs.FEDERATED_AGGREGATE.uri
    )
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_max_before_reduction, 0)
    self.assertEqual(count_max_after_reduction, 0)
    self.assertGreater(count_aggregations, 0)

  def test_federated_sum_reduces_to_aggregate(self):
    uri = intrinsic_defs.FEDERATED_SUM.uri

    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            computation_types.FederatedType(np.float32, placements.CLIENTS),
            computation_types.FederatedType(np.float32, placements.SERVER),
        ),
    )

    count_sum_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_sum_after_reduction = _count_intrinsics(reduced, uri)
    count_aggregations = _count_intrinsics(
        reduced, intrinsic_defs.FEDERATED_AGGREGATE.uri
    )
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_sum_before_reduction, 0)
    self.assertEqual(count_sum_after_reduction, 0)
    self.assertGreater(count_aggregations, 0)

  def test_generic_divide_reduces(self):
    uri = intrinsic_defs.GENERIC_DIVIDE.uri
    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType([np.float32, np.float32], np.float32),
    )

    count_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_after_reduction = _count_intrinsics(reduced, uri)

    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_before_reduction, 0)
    self.assertEqual(count_after_reduction, 0)
    tree_analysis.check_contains_only_reducible_intrinsics(reduced)

  def test_generic_multiply_reduces(self):
    uri = intrinsic_defs.GENERIC_MULTIPLY.uri
    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType([np.float32, np.float32], np.float32),
    )

    count_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_after_reduction = _count_intrinsics(reduced, uri)

    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_before_reduction, 0)
    self.assertEqual(count_after_reduction, 0)
    tree_analysis.check_contains_only_reducible_intrinsics(reduced)

  def test_generic_plus_reduces(self):
    uri = intrinsic_defs.GENERIC_PLUS.uri
    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType([np.float32, np.float32], np.float32),
    )

    count_before_reduction = _count_intrinsics(comp, uri)
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    count_after_reduction = _count_intrinsics(reduced, uri)

    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(count_before_reduction, 0)
    self.assertEqual(count_after_reduction, 0)
    tree_analysis.check_contains_only_reducible_intrinsics(reduced)

  @parameterized.named_parameters(
      ('int32', np.int32, np.int32),
      ('int32_struct', [np.int32, np.int32], np.int32),
      ('int64', np.int64, np.int32),
      ('mixed_struct', [np.int64, [np.int32]], np.int32),
      ('per_leaf_bitwidth', [np.int64, [np.int32]], [np.int32, [np.int32]]),
  )
  def test_federated_secure_sum(self, value_dtype, bitwidth_type):
    uri = intrinsic_defs.FEDERATED_SECURE_SUM.uri
    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            [
                computation_types.FederatedType(
                    value_dtype, placements.CLIENTS
                ),
                computation_types.to_type(bitwidth_type),
            ],
            computation_types.FederatedType(value_dtype, placements.SERVER),
        ),
    )
    self.assertGreater(_count_intrinsics(comp, uri), 0)
    # First without secure intrinsics shouldn't modify anything.
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    self.assertFalse(modified)
    self.assertGreater(_count_intrinsics(comp, uri), 0)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    # Now replace bodies including secure intrinsics.
    reduced, modified = (
        tensorflow_tree_transformations.replace_secure_intrinsics_with_insecure_bodies(
            comp
        )
    )
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(
        _count_intrinsics(reduced, intrinsic_defs.FEDERATED_AGGREGATE.uri), 0
    )

  @parameterized.named_parameters(
      ('int32', np.int32, np.int32),
      ('int32_struct', [np.int32, np.int32], np.int32),
      ('int64', np.int64, np.int32),
      ('mixed_struct', [np.int64, [np.int32]], np.int32),
      ('per_leaf_bitwidth', [np.int64, [np.int32]], [np.int32, [np.int32]]),
  )
  def test_federated_secure_sum_bitwidth(self, value_dtype, bitwidth_type):
    uri = intrinsic_defs.FEDERATED_SECURE_SUM_BITWIDTH.uri
    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            parameter=[
                computation_types.FederatedType(
                    value_dtype, placements.CLIENTS
                ),
                computation_types.to_type(bitwidth_type),
            ],
            result=computation_types.FederatedType(
                value_dtype, placements.SERVER
            ),
        ),
    )
    # First without secure intrinsics shouldn't modify anything.
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    self.assertFalse(modified)
    self.assertGreater(_count_intrinsics(comp, uri), 0)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    # Now replace bodies including secure intrinsics.
    reduced, modified = (
        tensorflow_tree_transformations.replace_secure_intrinsics_with_insecure_bodies(
            comp
        )
    )
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(
        _count_intrinsics(reduced, intrinsic_defs.FEDERATED_AGGREGATE.uri), 0
    )

  @parameterized.named_parameters(
      ('int32', np.int32, np.int32),
      ('int32_struct', [np.int32, np.int32], np.int32),
      ('int64', np.int32, np.int32),
      ('mixed_struct', [np.int32, [np.int32]], np.int32),
      ('per_leaf_modulus', [np.int32, [np.int32]], [np.int32, [np.int32]]),
  )
  def test_federated_secure_modular_sum(self, value_dtype, modulus_type):
    uri = intrinsic_defs.FEDERATED_SECURE_MODULAR_SUM.uri
    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            parameter=[
                computation_types.FederatedType(
                    value_dtype, placements.CLIENTS
                ),
                computation_types.to_type(modulus_type),
            ],
            result=computation_types.FederatedType(
                value_dtype, placements.SERVER
            ),
        ),
    )
    # First without secure intrinsics shouldn't modify anything.
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    self.assertFalse(modified)
    self.assertGreater(_count_intrinsics(comp, uri), 0)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    # Now replace bodies including secure intrinsics.
    reduced, modified = (
        tensorflow_tree_transformations.replace_secure_intrinsics_with_insecure_bodies(
            comp
        )
    )
    self.assertTrue(modified)
    # Inserting tensorflow, as we do here, does not preserve python containers
    # currently.
    type_test_utils.assert_types_equivalent(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(
        _count_intrinsics(reduced, intrinsic_defs.FEDERATED_SUM.uri), 0
    )

  def test_federated_secure_select(self):
    uri = intrinsic_defs.FEDERATED_SECURE_SELECT.uri
    comp = building_blocks.Intrinsic(
        uri,
        computation_types.FunctionType(
            [
                computation_types.FederatedType(
                    np.int32, placements.CLIENTS
                ),  # client_keys
                computation_types.FederatedType(
                    np.int32, placements.SERVER
                ),  # max_key
                computation_types.FederatedType(
                    np.float32, placements.SERVER
                ),  # server_state
                computation_types.FunctionType(
                    [np.float32, np.int32], np.float32
                ),  # select_fn
            ],
            computation_types.FederatedType(
                computation_types.SequenceType(np.float32), placements.CLIENTS
            ),
        ),
    )
    self.assertGreater(_count_intrinsics(comp, uri), 0)
    # First without secure intrinsics shouldn't modify anything.
    reduced, modified = (
        tensorflow_tree_transformations.replace_intrinsics_with_bodies(comp)
    )
    self.assertFalse(modified)
    self.assertGreater(_count_intrinsics(comp, uri), 0)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    # Now replace bodies including secure intrinsics.
    reduced, modified = (
        tensorflow_tree_transformations.replace_secure_intrinsics_with_insecure_bodies(
            comp
        )
    )
    self.assertTrue(modified)
    type_test_utils.assert_types_identical(
        comp.type_signature, reduced.type_signature
    )
    self.assertGreater(
        _count_intrinsics(reduced, intrinsic_defs.FEDERATED_SELECT.uri), 0
    )


if __name__ == '__main__':
  absltest.main()
