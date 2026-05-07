#!/usr/bin/env bash
set -euo pipefail

write_claude_delegate_json_file() {
    local path="$1"
    local data="$2"
    local dir
    dir=$(dirname "$path")
    local filename
    filename=$(basename "$path")
    local tmpfile="${dir}/.${filename}.$$.tmp"
    
    mkdir -p "$dir"
        local tmpfile

        mkdir -p "$dir"
        tmpfile=$(mktemp "${dir}/.${filename}.XXXXXX")
        echo "$data" > "$tmpfile"
        mv "$tmpfile" "$path"
}

#!/usr/bin/env bash
set -euo pipefail

write_claude_delegate_json_file() {
    local path="$1"
    local data="$2"
    local dir
    dir=$(dirname "$path")
    local filename
    filename=$(basename "$path")

    mkdir -p "$dir"
    local tmpfile
    tmpfile=$(mktemp "${dir}/.${filename}.XXXXXX")
    echo "$data" > "$tmpfile"
    mv "$tmpfile" "$path"
}

test_claude_delegate_text_has_final_result_heading() {
    local text="$1"
    if [[ -z "$text" ]]; then
        echo "false"
        return
    fi

    if echo "$text" | grep -qE '^(#+\s*)?Final Result\s*$'; then
        echo "true"
    else
        echo "false"
    fi
}

test_claude_delegate_has_final_result() {
    local path="$1"
    if [[ -z "$path" ]] || [[ ! -f "$path" ]]; then
        echo "false"
        return
    fi

    local content
    content=$(cat "$path")
    test_claude_delegate_text_has_final_result_heading "$content"
}

convert_claude_delegate_unstructured_final_text() {
    local text="$1"
    local trimmed_text
    if [[ -z "$text" ]]; then
        echo ""
        return
    fi
    trimmed_text=$(echo "$text" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')

    if [[ -z "$trimmed_text" ]]; then
        echo ""
        return
    fi

    local has_final
    has_final=$(test_claude_delegate_text_has_final_result_heading "$trimmed_text")
    if [[ "$has_final" == "true" ]]; then
        echo "$trimmed_text"
        return
    fi

    cat <<EOF
Process Log
- Claude returned a successful response without the required delegate report headings.
- The delegate wrapper normalized that response into the required structured report envelope.

Summary
Claude completed successfully, but its final response did not use the required report template. The original response is preserved under Final Result.

Changed Files
Unknown from unstructured response; inspect git diff and raw delegate artifacts before accepting file-level conclusions.

Verification
Unknown from unstructured response; do not treat verification as proven unless the original response below lists exact commands and outcomes.

Final Result
UNSTRUCTURED_SUCCESS_NORMALIZED
$trimmed_text

Risks Or Follow-ups
- Review the raw stream, trace, and repository diff before accepting verification-sensitive changes.
EOF
}

get_claude_delegate_output_resolution() {
    local final_text="$1"
    local output_path="$2"
    local exit_code="$3"
    local saw_result_success="$4"
    local captured_final_result_heading="$5"

    local final_text_has_final
    final_text_has_final=$(test_claude_delegate_text_has_final_result_heading "$final_text")
    local existing_structured_output="false"
    if [[ -n "$output_path" ]] && [[ -f "$output_path" ]]; then
        existing_structured_output=$(test_claude_delegate_has_final_result "$output_path")
    fi

    local output_was_normalized="false"
    if [[ "$exit_code" == "0" ]] && \
       [[ "$saw_result_success" == "true" ]] && \
       [[ "$final_text_has_final" == "false" ]] && \
       [[ "$existing_structured_output" == "false" ]] && \
       [[ -n "$final_text" ]]; then
        output_was_normalized="true"
    fi

    local persisted_final_text="$final_text"
    if [[ "$output_was_normalized" == "true" ]]; then
        persisted_final_text=$(convert_claude_delegate_unstructured_final_text "$final_text")
    fi

    local persisted_text_has_final
    persisted_text_has_final=$(test_claude_delegate_text_has_final_result_heading "$persisted_final_text")

    local should_persist_final_text="false"
    if [[ "$persisted_text_has_final" == "true" ]]; then
        should_persist_final_text="true"
    elif [[ "$existing_structured_output" == "false" ]] && [[ -n "$final_text" ]]; then
        should_persist_final_text="true"
    fi

    local delegate_succeeded="false"
    if [[ "$exit_code" == "0" ]] && \
       [[ "$saw_result_success" == "true" ]] && \
       { [[ "$captured_final_result_heading" == "true" ]] || \
         [[ "$persisted_text_has_final" == "true" ]] || \
         [[ "$existing_structured_output" == "true" ]]; }; then
        delegate_succeeded="true"
    fi

    cat <<EOF
{
  "finalTextHasFinalResult": $final_text_has_final,
  "existingStructuredOutput": $existing_structured_output,
  "outputWasNormalized": $output_was_normalized,
  "persistedFinalText": $(echo "$persisted_final_text" | jq -Rs .),
  "shouldPersistFinalText": $should_persist_final_text,
  "delegateSucceeded": $delegate_succeeded
}
EOF
}

is_process_alive() {
    local pid="$1"
    if [[ -z "$pid" ]] || [[ "$pid" -le 0 ]] 2>/dev/null; then
        echo "false"
        return
    fi

    if [[ -d "/proc/$pid" ]]; then
        echo "true"
    else
        echo "false"
    fi
}

test_claude_delegate_path_writable() {
    local path="$1"
    local full_path
    full_path=$(cd "$(dirname "$path")" 2>/dev/null && pwd)/$(basename "$path") || full_path="$path"
    local dir
    dir=$(dirname "$full_path")

    mkdir -p "$dir"

    local probe_path="${dir}/.write_probe_$$_$(date +%s%N).tmp"
    if echo "ok" > "$probe_path" 2>/dev/null; then
        rm -f "$probe_path"
        echo "true"
    else
        echo "false"
    fi
}

get_claude_delegate_text_blocks() {
    local content="$1"

    if [[ -z "$content" ]]; then
        echo ""
        return
    fi

    echo "$content" | jq -r '
        if type == "array" then
            .[] | select(.type == "text") | .text
        elif type == "object" and .type == "text" then
            .text
        else
            empty
        end
    ' 2>/dev/null || echo ""
}

update_claude_delegate_stream_capture() {
    local record="$1"
    local state_file="$2"

    local record_type
    record_type=$(echo "$record" | jq -r '.type // empty' 2>/dev/null || echo "")

    local trace_lines=""

    case "$record_type" in
        system)
            local subtype status
            subtype=$(echo "$record" | jq -r '.subtype // empty' 2>/dev/null || echo "")
            status=$(echo "$record" | jq -r '.status // empty' 2>/dev/null || echo "")
            trace_lines="[system] $subtype $status"
            ;;
        assistant)
            local message message_id
            message=$(echo "$record" | jq -c '.message // empty' 2>/dev/null || echo "")
            message_id=$(echo "$message" | jq -r '.id // empty' 2>/dev/null || echo "")

            if [[ -n "$message_id" ]]; then
                trace_lines="[assistant] message=$message_id"
            else
                trace_lines="[assistant]"
            fi

            local texts
            texts=$(echo "$message" | jq -r '.content[] | select(.type == "text") | .text' 2>/dev/null || echo "")
            if [[ -n "$texts" ]]; then
                local combined_text
                combined_text=$(echo "$texts" | tr '\n' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

                local has_final
                has_final=$(test_claude_delegate_text_has_final_result_heading "$combined_text")

                jq -n \
                    --argjson state "$(cat "$state_file" 2>/dev/null || echo '{}')" \
                    --arg text "$combined_text" \
                    --arg trace "$trace_lines" \
                    --argjson has_final "$has_final" '
                        $state + {
                            sawAssistantText: true,
                            capturedFinalResultHeading: $has_final,
                            finalText: $text
                        }
                    ' > "${state_file}.tmp" && mv "${state_file}.tmp" "$state_file"
            fi
            ;;
        result)
            local subtype cost
            subtype=$(echo "$record" | jq -r '.subtype // empty' 2>/dev/null || echo "")
            cost=$(echo "$record" | jq -r '.cost_usd // empty' 2>/dev/null || echo "")

            trace_lines="[result] $subtype cost=$cost"

            if [[ "$subtype" == "success" ]]; then
                jq -n \
                    --argjson state "$(cat "$state_file" 2>/dev/null || echo '{}')" \
                    '$state + {sawResultSuccess: true}' > "${state_file}.tmp" && mv "${state_file}.tmp" "$state_file"
            fi
            ;;
        stream_event)
            local event event_type
            event=$(echo "$record" | jq -c '.event // empty' 2>/dev/null || echo "")
            event_type=$(echo "$event" | jq -r '.type // empty' 2>/dev/null || echo "")
            if [[ -n "$event_type" ]]; then
                trace_lines="[stream] $event_type"
            else
                trace_lines="[stream]"
            fi
            ;;
        *)
            if [[ -n "$record_type" ]]; then
                trace_lines="[$record_type]"
            else
                trace_lines="[unknown-record]"
            fi
            ;;
    esac

    echo "$trace_lines"
}

get_claude_delegate_non_json_raw_lines() {
    local -a raw_lines=("$@")
    local non_json_lines=()

    for line in "${raw_lines[@]}"; do
        if [[ -z "$line" ]] || [[ "$line" =~ ^[[:space:]]*$ ]]; then
            continue
        fi

        if ! echo "$line" | jq -e . >/dev/null 2>&1; then
            non_json_lines+=("$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')")
        fi
    done

    printf '%s\n' "${non_json_lines[@]}"
}

get_claude_delegate_retry_decision() {
    local raw_lines="$1"
    local resume_attempt="$2"
    local exit_code="$3"
    local saw_assistant_text="$4"
    local saw_result_success="$5"
    local captured_final_result_heading="$6"

    local joined
    joined=$(echo "$raw_lines" | tr '\n' ' ')

    local saw_stale_session_text="false"
    if echo "$joined" | grep -qiE 'No conversation found.*session ID'; then
        saw_stale_session_text="true"
    fi

    local saw_stream_json_verbose_error="false"
    if echo "$joined" | grep -qiE 'stream-json.*requires.*--verbose'; then
        saw_stream_json_verbose_error="true"
    fi

    local has_structured_success="false"
    if [[ "$saw_result_success" == "true" ]] && [[ "$captured_final_result_heading" == "true" ]]; then
        has_structured_success="true"
    fi

    local should_retry="false"
    local retry_reason=""
    local retry_with_fresh_session="false"

    if [[ "$resume_attempt" == "true" ]] && \
       [[ "$saw_stale_session_text" == "true" ]] && \
       [[ "$has_structured_success" == "false" ]]; then
        should_retry="true"
        retry_reason="stale_claude_session"
        retry_with_fresh_session="true"
    elif [[ "$saw_stream_json_verbose_error" == "true" ]] && \
         [[ "$has_structured_success" == "false" ]]; then
        should_retry="true"
        retry_reason="stream_json_startup"
        retry_with_fresh_session="false"
    fi

    cat <<EOF
{
  "shouldRetry": $should_retry,
  "retryReason": "$retry_reason",
  "retryWithFreshSession": $retry_with_fresh_session,
  "sawStaleSessionText": $saw_stale_session_text,
  "sawStreamJsonVerboseError": $saw_stream_json_verbose_error,
  "hasStructuredSuccess": $has_structured_success,
  "exitCode": $exit_code,
  "sawAssistantText": $saw_assistant_text,
  "sawResultSuccess": $saw_result_success,
  "capturedFinalResultHeading": $captured_final_result_heading
}
EOF
}

get_claude_delegate_failure_summary() {
    local raw_lines="$1"
    local retry_reason="$2"
    local attempt_count="$3"
    local max_retry_count="$4"
    local exit_code="$5"

    local error_lines
    error_lines=$(echo "$raw_lines" | grep -v '^[[:space:]]*$' | grep -v '^{' | head -2 | tr '\n' ' | ' | sed 's/|[[:space:]]*$//')

    if [[ -z "$error_lines" ]]; then
        error_lines="No non-JSON stderr summary was captured."
    fi

    local reason_text="${retry_reason:-unknown_retry_condition}"
    local max_attempts=$((max_retry_count + 1))

    echo "NEED_HUMAN_INTERVENTION after exhausting retry budget. retryReason=$reason_text. attempt $attempt_count/$max_attempts. exitCode=$exit_code. $error_lines"
}

test_claude_delegate_needs_fresh_session_retry() {
    local raw_lines="$1"
    local resume_attempt="$2"

    local decision
    decision=$(get_claude_delegate_retry_decision "$raw_lines" "$resume_attempt" 1 false false false)

    local should_retry retry_with_fresh
    should_retry=$(echo "$decision" | jq -r '.shouldRetry')
    retry_with_fresh=$(echo "$decision" | jq -r '.retryWithFreshSession')

    if [[ "$should_retry" == "true" ]] && [[ "$retry_with_fresh" == "true" ]]; then
        echo "true"
    else
        echo "false"
    fi
}

