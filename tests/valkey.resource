*** Settings ***
Library    SSHLibrary

*** Keywords ***
Install Valkey
    ${output}  ${rc} =    Execute Command    add-module ${IMAGE_URL} 1
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0
    &{output} =    Evaluate    ${output}
    RETURN    ${output.module_id}

Run Action
    [Arguments]    ${module_id}    ${action}    ${data}={}
    ${output}  ${rc} =    Execute Command    api-cli run module/${module_id}/${action} --data '${data}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0    ${action} on ${module_id} failed
    IF    $output.strip() == '' or $output.strip() == 'null'
        RETURN    ${NONE}
    END
    ${result} =    Evaluate    json.loads($output)    modules=json
    RETURN    ${result}

Action Should Fail
    [Arguments]    ${module_id}    ${action}    ${data}={}
    ${rc} =    Execute Command    api-cli run module/${module_id}/${action} --data '${data}'
    ...    return_rc=True  return_stdout=False
    Should Not Be Equal As Integers    ${rc}  0

Copy Probe
    Put File    ${CURDIR}/valkey-probe.py    /tmp/valkey-probe.py    mode=0644

Grant Client
    [Documentation]    Run grant-client and keep its output (password included) in a
    ...                0600 file of the test node. Returns the output without the password.
    [Arguments]    ${module_id}    ${data}    ${file}=/root/valkey-grant.json
    ${rc} =    Execute Command    umask 077; api-cli run module/${module_id}/grant-client --data '${data}' > ${file}
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0    grant-client on ${module_id} failed
    ${output} =    Execute Command    python3 -c 'import json,sys; g=json.load(open(sys.argv[1])); g["password_length"]=len(g.pop("password")); print(json.dumps(g))' ${file}
    ${result} =    Evaluate    json.loads($output)    modules=json
    RETURN    ${result}

Probe
    [Documentation]    Run the test client as a module, with the saved grant on stdin
    [Arguments]    ${module_id}    ${op}    ${count}=100    ${file}=/root/valkey-grant.json
    ${output}  ${rc} =    Execute Command    runagent -m ${module_id} python3 /tmp/valkey-probe.py ${op} ${count} < ${file}
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0
    ${result} =    Evaluate    json.loads($output)    modules=json
    RETURN    ${result}

Wait Until Cluster Healthy
    [Arguments]    ${module_id}    ${members}    ${tolerance}
    ${status} =    Wait Until Keyword Succeeds    180s    5s    Cluster Should Be Healthy    ${module_id}    ${members}    ${tolerance}
    RETURN    ${status}

Cluster Should Be Healthy
    [Arguments]    ${module_id}    ${members}    ${tolerance}
    ${status} =    Run Action    ${module_id}    get-cluster-status
    Should Be Equal    ${status}[cluster_state]    ok
    Should Be Equal As Integers    ${status}[members_count]    ${members}
    Should Be Equal As Integers    ${status}[failure_tolerance]    ${tolerance}
    FOR    ${proc}    IN    @{status}[processes]
        Should Be True    ${proc}[reachable]
        IF    $proc["role"] == "replica"
            Should Be Equal    ${proc}[link]    up
        END
    END
    RETURN    ${status}
