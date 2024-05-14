# Copyright 2021, The TensorFlow Federated Authors.
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
"""Execution context for single-aggregation computations."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import functools
import math
from typing import Generic, Optional, TypeVar, Union

import attrs

from tensorflow_federated.python.common_libs import async_utils
from tensorflow_federated.python.common_libs import py_typecheck
from tensorflow_federated.python.common_libs import structure
from tensorflow_federated.python.core.impl.compiler import building_blocks
from tensorflow_federated.python.core.impl.compiler import tree_analysis
from tensorflow_federated.python.core.impl.computation import computation_base
from tensorflow_federated.python.core.impl.context_stack import context_base
from tensorflow_federated.python.core.impl.execution_contexts import compiler_pipeline
from tensorflow_federated.python.core.impl.executors import cardinalities_utils
from tensorflow_federated.python.core.impl.types import computation_types
from tensorflow_federated.python.core.impl.types import placements
from tensorflow_federated.python.core.impl.types import type_analysis
from tensorflow_federated.python.core.impl.types import type_conversions
from tensorflow_federated.python.core.impl.types import typed_object

# Type alias for the payload value in a partitioned data structure.
Value = TypeVar('Value')
_Computation = TypeVar('_Computation', bound=computation_base.Computation)


class MergeTypeNotAssignableError(TypeError):
  pass


class UpToMergeTypeError(TypeError):
  pass


class AfterMergeStructureError(ValueError):
  pass


class MergeableCompForm:
  """A data class for computations containing a single logical aggregation.

  `MergeableCompForm` contains three member computations, `up_to_merge`
  and `merge`, and `after_merge`. A computation in `MergeableCompForm` is
  defined to be equivalent to invoking `up_to_merge` on subsets of
  `CLIENTS`-placed arguments in sequence, invoking `merge` on the stream of
  these results, and passing the resulting value (placed at `tff.SERVER`) to
  `after_merge`, in addition to the original argument to `up_to_merge`.
  In the case of a no-arg `up_to_merge` computation, `after_merge` should accept
  only the result of `merge`. `up_to_merge` should return a single
  `tff.SERVER`-placed value.

  We require that computations in `MergeableCompForm` contain *no* AST
  nodes with signatures taking arguments at `tff.CLIENTS` and producing results
  at `tff.SERVER`.

  `MergeableCompForm` computations are often generated by aligning a computation
  containing a single logical aggregation on this aggregation, and splitting it
  along its merge boundary. That is, since `merge` can be invoked repeatedly
  without changing the results of the computation, a computation of the form:

  ```
  @tff.federated_computation(...)
  def single_aggregation(arg):
    result_at_clients = work(arg)
    agg_result = tff.federated_aggregate(
        result_at_clients, zero, accumulate, merge, report)
    return postprocess(arg, agg_result)
  ```

  can be represented as the `MergeableCompForm` triplet:
  ```
  @tff.federated_computation(tff.AbstractType('T'))
  def up_to_merge(arg):
    result_at_clients = work(arg)
    agg_result = tff.federated_aggregate(
        result_at_clients, accumulate_zero, accumulate, merge, identity_report)
    return agg_result

  @tff.federated_computation([up_to_merge.type_signature.result.member,
                              up_to_merge.type_signature.result.member])
  def merge(arg):
    return merge(arg[0], arg[1])

  @tff.federated_computation(
      tff.AbstractType('T'),
      tff.FederatedType(merge.type_signature.result, tff.SERVER),
  )
  def after_merge(original_arg, merged_result):
    return postprocess(original_arg, merged_result)
  ```

  A fair amount of complexity can be hidden in `postprocess`; it could, for
  example, compute some value on clients based on the result of the aggregation.
  But the restriction that `after_merge` can contain no aggregations ensures
  that `after_merge` can also be executed in a subround fashion: a
  `tff.CLIENTS`-placed result can only depend on its own local state and the
  result of the aggregation, while a `tff.SERVER`-placed result can only depend
  on the result of the single aggregation or a `tff.SERVER`-placed value.
  """

  def __init__(
      self,
      *,
      up_to_merge: computation_base.Computation,
      merge: computation_base.Computation,
      after_merge: computation_base.Computation,
  ):
    if not (
        isinstance(
            up_to_merge.type_signature.result, computation_types.FederatedType
        )
        and up_to_merge.type_signature.result.placement is placements.SERVER
    ):
      raise UpToMergeTypeError(
          'Expected `up_to_merge` to return a single `tff.SERVER`-placed '
          f'value; found return type {up_to_merge.type_signature.result}.'
      )

    # TFF's StructType assignability relation ensures that an unnamed struct can
    # be assigned to any struct with names.
    expected_merge_param_type = computation_types.StructType([
        (None, up_to_merge.type_signature.result.member),  # pytype: disable=attribute-error
        (None, up_to_merge.type_signature.result.member),  # pytype: disable=attribute-error
    ])
    if not merge.type_signature.parameter.is_assignable_from(
        expected_merge_param_type
    ):  # pytype: disable=attribute-error
      raise MergeTypeNotAssignableError(
          'Type mismatch checking `merge` type signature.\n'
          + computation_types.type_mismatch_error_message(
              merge.type_signature.parameter,
              expected_merge_param_type,
              computation_types.TypeRelation.ASSIGNABLE,
              second_is_expected=True,
          )
      )
    if not (
        merge.type_signature.parameter[0].is_assignable_from(
            merge.type_signature.result
        )  # pytype: disable=unsupported-operands
        and merge.type_signature.parameter[1].is_assignable_from(
            merge.type_signature.result
        )  # pytype: disable=unsupported-operands
    ):
      raise MergeTypeNotAssignableError(
          'Expected `merge` to have result which is assignable to '
          'each element of its parameter tuple; found parameter '
          f'of type: \n{merge.type_signature.parameter}\nAnd result of type: \n'
          f'{merge.type_signature.result}'
      )

    if up_to_merge.type_signature.parameter is not None:
      # TODO: b/147499373 - If None arguments were uniformly represented as
      # empty tuples, we could avoid this and related ugly if/else casing.
      expected_after_merge_arg_type = computation_types.StructType([
          (None, up_to_merge.type_signature.parameter),
          (
              None,
              computation_types.FederatedType(
                  merge.type_signature.result, placements.SERVER
              ),
          ),
      ])
    else:
      expected_after_merge_arg_type = computation_types.FederatedType(
          merge.type_signature.result, placements.SERVER
      )

    after_merge.type_signature.parameter.check_assignable_from(
        expected_after_merge_arg_type
    )  # pytype: disable=attribute-error

    def _federated_type_predicate(
        type_signature: computation_types.Type,
        placement: placements.PlacementLiteral,
    ) -> bool:
      return (
          isinstance(type_signature, computation_types.FederatedType)
          and type_signature.placement is placement
      )

    def _moves_clients_to_server_predicate(
        intrinsic: building_blocks.Intrinsic,
    ):
      parameter_contains_clients_placement = type_analysis.contains(
          intrinsic.type_signature.parameter,  # pytype: disable=attribute-error
          lambda x: _federated_type_predicate(x, placements.CLIENTS),
      )
      result_contains_server_placement = type_analysis.contains(
          intrinsic.type_signature.result,  # pytype: disable=attribute-error
          lambda x: _federated_type_predicate(x, placements.SERVER),
      )
      return (
          parameter_contains_clients_placement
          and result_contains_server_placement
      )

    aggregations = set()

    def _aggregation_predicate(
        comp: building_blocks.ComputationBuildingBlock,
    ) -> bool:
      if not isinstance(comp, building_blocks.Intrinsic):
        return False
      if not isinstance(comp.type_signature, computation_types.FunctionType):
        return False
      if _moves_clients_to_server_predicate(comp):
        aggregations.add((comp.uri, comp.type_signature))
        return True
      return False

    # We only know how to statically analyze computations which are backed by
    # computation.protos; to avoid opening up a visibility hole that isn't
    # technically necessary here, we prefer to simply skip the static check here
    # for computations which cannot convert themselves to building blocks.
    if hasattr(after_merge, 'to_building_block') and tree_analysis.contains(
        after_merge.to_building_block(), _aggregation_predicate
    ):
      formatted_aggregations = ', '.join(
          '{}: {}'.format(elem[0], elem[1]) for elem in aggregations
      )
      raise AfterMergeStructureError(
          'Expected `after_merge` to contain no intrinsics '
          'with signatures accepting values at clients and '
          'returning values at server. Found the following '
          f'aggregations: {formatted_aggregations}'
      )

    self.up_to_merge = up_to_merge
    self.merge = merge
    self.after_merge = after_merge


@attrs.define
class _PartitioningValue:
  """Data class to hold info on traversal while partitioning into subrounds."""

  payload: object
  num_remaining_clients: int
  num_remaining_partitions: int
  last_client_index: int


def _partition_value(
    val: _PartitioningValue, type_signature: computation_types.Type
) -> _PartitioningValue:
  """Partitions value as specified in _split_value_into_subrounds."""
  if isinstance(type_signature, computation_types.StructType):
    struct_val = structure.from_container(val.payload)
    partition_result: Optional[_PartitioningValue] = None
    result_container = []
    for (_, val_elem), (name, type_elem) in zip(
        structure.iter_elements(struct_val),
        structure.iter_elements(type_signature),
    ):
      partitioning_val_elem = _PartitioningValue(
          val_elem,
          val.num_remaining_clients,
          val.num_remaining_partitions,
          val.last_client_index,
      )
      partition_result = _partition_value(partitioning_val_elem, type_elem)
      result_container.append((name, partition_result.payload))
    if partition_result is None:
      raise ValueError(f'Expected the value to not be empty, found {val}.')
    return _PartitioningValue(
        structure.Struct(result_container),
        partition_result.num_remaining_clients,
        partition_result.num_remaining_partitions,
        partition_result.last_client_index,
    )
  elif (
      isinstance(type_signature, computation_types.FederatedType)
      and type_signature.placement is placements.CLIENTS
  ):
    if type_signature.all_equal:
      # In this case we simply replicate the argument for every subround.
      return val

    py_typecheck.check_type(val.payload, Sequence)
    num_clients_for_subround = math.ceil(
        val.num_remaining_clients / val.num_remaining_partitions
    )
    num_remaining_clients = val.num_remaining_clients - num_clients_for_subround
    num_remaining_partitions = val.num_remaining_partitions - 1
    values_to_return = val.payload[
        val.last_client_index : val.last_client_index + num_clients_for_subround
    ]
    last_client_index = val.last_client_index + num_clients_for_subround
    return _PartitioningValue(
        payload=values_to_return,
        num_remaining_clients=num_remaining_clients,
        num_remaining_partitions=num_remaining_partitions,
        last_client_index=last_client_index,
    )
  else:
    return val


def _split_value_into_subrounds(
    value: Value, type_spec: computation_types.Type, num_desired_subrounds: int
) -> list[Value]:
  """Partitions clients-placed values to subrounds, replicating other values.

  This function should be applied to an argument of a computation which is
  intended to be executed in subrounds; the semantics of this use case drive
  the implementation of this function.

  This function will return a list of values representing the appropriate
  arguments to subrounds. Any value which is not CLIENTS-placed of not-all-equal
  type will be replicated in the return value of this function. The returned
  list will be up to `num_desired_subrounds` elements in length, possibly
  shorter if the cardinality of clients represented by `value` is less than
  `num_desired_subrounds`, to avoid constructing empty clients-placed arguments.

  Args:
    value: The argument to a computation intended to be invoked in subrounds,
      which will be partitioned. `value` can be any structure understood by
      TFF's native execution contexts.
    type_spec: The `computation_types.Type` corresponding to `value`.
    num_desired_subrounds: Int specifying the desired number of subrounds to
      run. Specifies the maximum length of the returned list.

  Returns:
    A list of partitioned values as described above.
  """
  cardinalities = cardinalities_utils.infer_cardinalities(value, type_spec)
  if cardinalities.get(placements.CLIENTS) is None:
    # The argument contains no clients-placed values, but may still perform
    # nontrivial clients-placed work.
    return [value for _ in range(num_desired_subrounds)]
  elif cardinalities[placements.CLIENTS] == 0:
    # Here the argument contains an empty clients-placed value; therefore this
    # computation should be run over an empty set of clients.
    return [value]

  partitioning_value = _PartitioningValue(
      payload=value,
      num_remaining_clients=cardinalities[placements.CLIENTS],
      num_remaining_partitions=num_desired_subrounds,
      last_client_index=0,
  )

  values_to_return = []
  for _ in range(num_desired_subrounds):
    if partitioning_value.num_remaining_clients > 0:
      partitioned_value = _partition_value(partitioning_value, type_spec)
      values_to_return.append(partitioned_value.payload)
      partitioning_value = _PartitioningValue(
          partitioning_value.payload,
          num_remaining_clients=partitioned_value.num_remaining_clients,
          num_remaining_partitions=partitioned_value.num_remaining_partitions,
          last_client_index=partitioned_value.last_client_index,
      )
    else:
      # Weve already partitioned all the clients we can. The underlying
      # execution contexts will fail if we pass them empty clients-placed
      # values.
      break

  return values_to_return


def _repackage_partitioned_values(
    after_merge_results: Union[list[Value], tuple[Value, ...]],
    result_type_spec: computation_types.Type,
) -> Value:
  """Inverts `_split_value_into_subrounds` above."""
  py_typecheck.check_type(after_merge_results, (tuple, list))
  if isinstance(result_type_spec, computation_types.StructType):
    after_merge_structs = [
        structure.from_container(x) for x in after_merge_results
    ]
    result_container = []
    for idx, (name, elem_type) in enumerate(
        structure.iter_elements(result_type_spec)
    ):
      result_container.append((
          name,
          _repackage_partitioned_values(
              [x[idx] for x in after_merge_structs], elem_type
          ),
      ))
    return structure.Struct(result_container)
  elif (
      isinstance(result_type_spec, computation_types.FederatedType)
      and result_type_spec.placement is placements.CLIENTS
  ):
    if result_type_spec.all_equal:
      return after_merge_results[0]
    for x in after_merge_results:
      py_typecheck.check_type(x, (list, tuple))
    # Merges all clients-placed values back together.
    return functools.reduce(lambda x, y: x + y, after_merge_results)
  else:
    return after_merge_results[0]


class MergeableCompExecutionContextValue(typed_object.TypedObject):
  """Represents a value embedded in the `MergeableCompExecutionContext`."""

  def __init__(
      self,
      value: object,
      type_spec: computation_types.Type,
      num_desired_subrounds: int,
  ):
    py_typecheck.check_type(type_spec, computation_types.Type)
    self._type_signature = type_spec
    self._partitioned_value = _split_value_into_subrounds(
        value, self._type_signature, num_desired_subrounds=num_desired_subrounds
    )

  @property
  def type_signature(self):
    return self._type_signature

  def value(self):
    return self._partitioned_value


async def _invoke_up_to_merge_and_return_context(
    comp: MergeableCompForm, arg, context: context_base.AsyncContext
):
  return await context.invoke(
      comp.up_to_merge,  # pytype: disable=attribute-error
      arg,
  )


async def _merge_results(
    comp: MergeableCompForm,
    merge_partial,
    value_to_merge,
    context: context_base.AsyncContext,
):
  return await context.invoke(
      comp.merge,  # pytype: disable=attribute-error
      structure.Struct.unnamed(merge_partial, value_to_merge),
  )


async def _compute_after_merged(
    comp: MergeableCompForm,
    original_arg,
    merge_result,
    context: context_base.AsyncContext,
):
  if original_arg is not None:
    arg = structure.Struct.unnamed(original_arg, merge_result)
  else:
    arg = merge_result
  return await context.invoke(
      comp.after_merge,  # pytype: disable=attribute-error
      arg,
  )


async def _run_in_async_context_pool(
    task_fn: Callable[[object, context_base.AsyncContext], asyncio.Task],
    arg_list: Sequence[object],
    execution_contexts: Sequence[context_base.AsyncContext],
    initial_result: object,
    postprocessing_hook: Callable[
        [object, object, context_base.AsyncContext], Awaitable[Value]
    ],
) -> tuple[Value, Optional[context_base.AsyncContext]]:
  """Runs the tasks against the execution pool, sequentializing the extra work.

  Args:
    task_fn: A callable which accepts an argument and a context, returning an
      `asyncio.Task`. This task itself is expected to return both a result and
      the context which computed this result (and thus has completed its work).
    arg_list: The sequence of arguments to feed into task_fn. When this sequence
      is processed, the function will return.
    execution_contexts: The sequence of asynchronous TFF contexts in which to
      compute these results. If this sequence is shorter than `arg_list`, we
      will sequentialize calls with the excess arguments.
    initial_result: The initial result to pass to the first postprocessing call.
    postprocessing_hook: A callback which process results. Will be called with
      the result of the previous postprocessing call, the new result (returned
      by the most recently completed invocation), and a context ready to
      execute.

  Returns:
    The result of the final postprocessing call, and the context used to compute
    this result.
  """
  contexts_by_task = {
      task_fn(x, context): context
      for x, context in zip(arg_list, execution_contexts)
  }
  arg_list_index = len(execution_contexts)
  result = initial_result
  context = None
  pending_tasks = set(contexts_by_task.keys())

  while pending_tasks:
    done, pending_tasks = await asyncio.wait(
        pending_tasks, return_when=asyncio.FIRST_COMPLETED
    )
    for done_task in done:
      context = contexts_by_task[done_task]
      partial_result = done_task.result()
      result = await postprocessing_hook(result, partial_result, context)
      if arg_list_index < len(arg_list):
        # There are still args to be processed; add a new invocation to
        # pending_tasks and increment arg_list_index.
        task = task_fn(arg_list[arg_list_index], context)
        pending_tasks.add(task)
        contexts_by_task[task] = context
        arg_list_index += 1

  return result, context


async def _invoke_merge_in_async_pool(
    comp: MergeableCompForm,
    arg_list: Sequence[object],
    execution_contexts: Sequence[context_base.AsyncContext],
):
  """Invokes up to merge and merge in a pool of async contexts."""

  def task_fn(x, context):
    return asyncio.create_task(
        _invoke_up_to_merge_and_return_context(comp, x, context)
    )

  initial_result = None

  async def postprocessing(result, partial_result, context):
    # We run merge on the partial results.
    if result is None:
      return partial_result
    else:
      return await _merge_results(comp, result, partial_result, context)

  return await _run_in_async_context_pool(
      task_fn, arg_list, execution_contexts, initial_result, postprocessing
  )


async def _invoke_after_merge_in_async_pool(
    comp: MergeableCompForm,
    merge_result: object,
    arg_list: Sequence[object],
    execution_contexts: Sequence[context_base.AsyncContext],
) -> list[object]:
  """Invokes after_merge in a pool of async contexts, returning result."""

  def task_fn(x, context):
    return asyncio.create_task(
        _compute_after_merged(comp, x, merge_result, context)
    )

  initial_result = ()

  async def postprocessing(result, partial_result, context):
    del context  # Unused
    # We simply concatenate all the results in a new tuple for repackaging.
    return (*result, partial_result)

  after_merge_results, _ = await _run_in_async_context_pool(
      task_fn, arg_list, execution_contexts, initial_result, postprocessing
  )
  return list(after_merge_results)


async def _invoke_mergeable_comp_form(
    comp: MergeableCompForm,
    arg: Optional[MergeableCompExecutionContextValue],
    execution_contexts: Sequence[context_base.AsyncContext],
):
  """Invokes `comp` on `arg`, repackaging the results to a single value."""

  if arg is not None:
    arg_list = arg.value()
  else:
    arg_list = [None for _ in range(len(execution_contexts))]

  merge_result, merge_context = await _invoke_merge_in_async_pool(
      comp, arg_list, execution_contexts
  )

  def _predicate(type_spec: computation_types.Type) -> bool:
    return (
        not isinstance(type_spec, computation_types.FederatedType)
        or type_spec.all_equal
    )

  if type_analysis.contains_only(
      comp.after_merge.type_signature.result,  # pytype: disable=attribute-error
      _predicate,
  ):
    # In this case, all contexts must return the same result, which must
    # therefore be independent of which element in the arg_list is passed--so we
    # avoid the extra work.
    top_level_arg_slice = arg_list[0]
    result = await _compute_after_merged(
        comp, top_level_arg_slice, merge_result, merge_context
    )
    return result

  after_merge_results = await _invoke_after_merge_in_async_pool(
      comp, merge_result, arg_list, execution_contexts
  )

  repackaged_values = _repackage_partitioned_values(
      after_merge_results,
      result_type_spec=comp.after_merge.type_signature.result,  # pytype: disable=attribute-error
  )
  return repackaged_values


class MergeableCompExecutionContext(
    context_base.SyncContext, Generic[_Computation]
):
  """Context which executes mergeable computations in subrounds.

  This context relies on retrying behavior of the  underlying asynchronous
  execution contexts to localize retries to these subrounds. That is, if a
  subround fails, this subround is the only computation that is retried. This
  allows `MergeableCompExecutionContext` to execute larger rounds than a
  runtime which retries the entire round in the case of e.g. a worker failure.
  """

  def __init__(
      self,
      async_contexts: Sequence[context_base.AsyncContext],
      compiler_fn: Optional[Callable[[_Computation], MergeableCompForm]] = None,
      num_subrounds: Optional[int] = None,
  ):
    """Initializes a MergeableCompExecutionContext.

    Args:
      async_contexts: Sequence of TFF execution contexts. These contexts are
        assumed to implement their `invoke` method as a coroutine function,
        returning an awaitable.
      compiler_fn: An optional callable which accepts a
        `tff.framework.ConcreteComputation` and returns an instance of
        `MergeableCompForm`. If not provided, this context will only execute
        instances of `MergeableCompForm` directly.
      num_subrounds: An optional integer, specifying total the number of
        subrounds desired. If unspecified, the length of `async_contexts` will
        determine the number of subrounds. If more subrounds are requested than
        contexts are passed, invocations will be sequentialized.
    """
    self._async_runner = async_utils.AsyncThreadRunner()
    for ctx in async_contexts:
      py_typecheck.check_type(ctx, context_base.AsyncContext)
    self._async_execution_contexts = async_contexts
    self._num_subrounds = (
        num_subrounds
        if num_subrounds is not None
        else len(self._async_execution_contexts)
    )
    if compiler_fn is not None:
      self._compiler_pipeline = compiler_pipeline.CompilerPipeline(compiler_fn)
    else:
      self._compiler_pipeline = None

  def invoke(
      self,
      comp: Union[MergeableCompForm, computation_base.Computation],
      arg: Optional[object] = None,
  ):
    py_typecheck.check_type(
        comp, (MergeableCompForm, computation_base.Computation)
    )
    if isinstance(comp, computation_base.Computation):
      if self._compiler_pipeline is None:
        raise ValueError(
            'Without a compiler, mergeable comp execution context '
            'can only invoke instances of MergeableCompForm. '
            'Encountered a `tff.Computation`.'
        )
      comp = self._compiler_pipeline.compile(comp)
      if not isinstance(comp, MergeableCompForm):
        raise ValueError(
            'Expected compilation in mergeable comp execution '
            'context to produce an instance of MergeableCompForm; '
            f'found instead {comp} of Python type {type(comp)}.'
        )

    if arg is not None:
      arg = MergeableCompExecutionContextValue(
          arg,
          comp.up_to_merge.type_signature.parameter,  # pytype: disable=attribute-error
          self._num_subrounds,
      )

    return type_conversions.type_to_py_container(
        self._async_runner.run_coro_and_return_result(
            _invoke_mergeable_comp_form(
                comp, arg, self._async_execution_contexts
            )
        ),
        comp.after_merge.type_signature.result,  # pytype: disable=attribute-error
    )