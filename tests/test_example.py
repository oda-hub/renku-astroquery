import subprocess
import shutil
import os

# make fixture
def fetch_example_oda_repo(fresh=False, reset=True) -> str:
    repo_name = "renku-aqs-test-case"
    repo_dir = repo_name

    if os.path.exists(repo_dir):
        if fresh:
            shutil.rmtree(repo_dir)
        else:
            if reset:
                subprocess.check_call(
                    ["git", "reset", "--hard"],
                    cwd=repo_dir
                )        
                subprocess.check_call(
                    ["git", "clean", "-f", "-d"],
                    cwd=repo_dir
                )        
            
            return repo_dir

    subprocess.check_call(
        ["git", "clone", f"git@github.com:volodymyrss/{repo_name}.git", repo_dir]
    )
    subprocess.check_call(
        ["git", "lfs", "install", "--local"],
        cwd=repo_dir
    )
    
    return repo_dir


# on purpose from shell
def run_renku_cli(cmd: list, repo_dir: str):
    

    subprocess.check_call(["renku", "run"] + cmd, cwd=repo_dir, env=dict(
            {**os.environ, "PYTHONPATH": ".:"+os.environ.get('PYTHONPATH')}
        ))


def test_example_oda_repo_code_py():
    repo_dir = fetch_example_oda_repo()

    run_renku_cli(["python", "example_code.py", "--output", "test-output.txt"], repo_dir=repo_dir)

    #TODO:
    #renku aqs params
    #rdf2dot renku-aqs-test-case/subgraph.ttl  | dot -Tpng -o subgraph.png
    # add references to the workflow identity and location

def test_example_oda_repo_papermill():
    repo_dir = fetch_example_oda_repo()

    run_renku_cli(["papermill", "final-an.ipynb", "out.ipynb", "--output", "test-output.txt"], repo_dir=repo_dir)