/** Narrow generated application-protocol results at feature action boundaries. */
import type {
  CommandMethod,
  CommandResult,
  QueryMethod,
  QueryResult,
} from '../generated/applicationProtocol.js';

export function asQueryResult<M extends QueryMethod, Result>(result: QueryResult<M>): Result {
  return result as unknown as Result;
}

export function asCommandResult<M extends CommandMethod, Result>(result: CommandResult<M>): Result {
  return result as unknown as Result;
}
