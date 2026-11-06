% Release ordering on the Grid's two bottlenecks, written independently of the CP-SAT model in
% q2s/policy/schedule.py so the two can be checked against each other (tests/test_release_models.py).
%
% Each order runs on k_pl placers for d_pl minutes and then in the manual cell on k_op operators for
% d_op minutes. Panels reach the manual cell as they come off the placers. Work already on the floor
% keeps every placer busy for wip_pl minutes and every operator for wip_op minutes.
% Minimise value-weighted tardiness (times tardy_weight), then value-weighted completion time.
include "cumulative.mzn";

int: n;
set of int: JOB = 1..n;
array[JOB] of int: d_pl;
array[JOB] of int: k_pl;
array[JOB] of int: d_op;
array[JOB] of int: k_op;
array[JOB] of int: due;
array[JOB] of int: weight;
int: placers;
int: operators;
int: wip_pl;
int: wip_op;
int: horizon;
int: handoff;
int: finish_gap;
int: tardy_weight;

array[JOB] of var 0..horizon: s_pl;
array[JOB] of var 0..horizon: s_op;
array[JOB] of var 0..horizon + max(d_op): e_op = [s_op[j] + d_op[j] | j in JOB];
% An order already overdue is late by more than the horizon; its tardiness must still fit.
array[JOB] of var 0..horizon + max(d_op) + max(0, -min(due)): tard;

% Placers: the work on the floor first, as one block using every machine.
constraint cumulative([0] ++ s_pl, [wip_pl] ++ d_pl, [placers] ++ k_pl, placers);
constraint cumulative([0] ++ s_op, [wip_op] ++ d_op, [operators] ++ k_op, operators);
constraint forall(j in JOB)(s_pl[j] + d_pl[j] <= horizon);
constraint forall(j in JOB)(s_op[j] >= s_pl[j] + d_pl[j] div 2 + handoff);
constraint forall(j in JOB)(e_op[j] >= s_pl[j] + d_pl[j] + finish_gap);
constraint forall(j in JOB)(tard[j] >= e_op[j] - due[j]);

var int: objective = sum(j in JOB)(tardy_weight * weight[j] * tard[j] + weight[j] * e_op[j]);
solve minimize objective;

output ["objective=\(objective)\n", "s_pl=\(s_pl)\n"];
