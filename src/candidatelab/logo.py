
import time


def print_banner(animate=True, stream=None):

    RED = "\033[91m"
    GREEN = "\033[32m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    banner = f"""{BOLD}{RED}
 ██████╗ ██╗    ██╗███╗   ██╗███████╗██████╗ 
 ██╔══██╗██║    ██║████╗  ██║██╔════╝██╔══██╗
 ██████╔╝██║ █╗ ██║██╔██╗ ██║█████╗  ██║  ██║
 ██╔═══╝ ██║███╗██║██║╚██╗██║██╔══╝  ██║  ██║
 ██║     ╚███╔███╔╝██║ ╚████║███████╗██████╔╝
 ╚═╝      ╚══╝╚══╝ ╚═╝  ╚═══╝╚══════╝╚══════╝ 
{GREEN}
 [!] PREDICTIVE WORDLIST NETWORK & ENTITY DISCOVERY
 
{RESET}"""

    for line in banner.split("\n"):
        print(line, file=stream)
        if animate:
            time.sleep(0.04)


if __name__ == "__main__":
    print_banner()
