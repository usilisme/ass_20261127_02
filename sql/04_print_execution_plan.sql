-- =============================================================================
-- 04_print_execution_plan.sql
-- Prints UNIT_NBR and STEP_PROG_NAME in execution order.
-- Steps with the same EXEC_STAGE have no dependency on each other and may run
-- in parallel; within a stage they are listed by STEP_SEQ_ID.
-- =============================================================================

SELECT unit_nbr       AS UNIT_NBR,
       exec_stage     AS EXEC_STAGE,
       step_prog_name AS STEP_PROG_NAME
FROM   v_execution_plan
ORDER  BY unit_nbr, exec_stage, step_seq_id;
