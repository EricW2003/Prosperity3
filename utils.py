import os
import sys
import pandas as pd

def clear_backtests_logs(backtests_dir: str = "backtests"):
    """
    Supprime tous les fichiers .log du dossier spécifié.

    Args:
        backtests_dir (str): Chemin relatif vers le dossier contenant les logs (par défaut: "backtests")
    """
    if not os.path.exists(backtests_dir):
        print(f"Le dossier {backtests_dir} n'existe pas.")
        return

    log_files = [f for f in os.listdir(backtests_dir) if f.endswith('.log')]

    if not log_files:
        print(f"Aucun fichier .log trouvé dans {backtests_dir}.")
        return

    for log_file in log_files:
        file_path = os.path.join(backtests_dir, log_file)
        try:
            os.remove(file_path)
            print(f"Supprimé: {log_file}")
        except Exception as e:
            print(f"Erreur lors de la suppression de {log_file}: {e}")

    print(f"Nettoyage terminé. {len(log_files)} fichiers supprimés.")

def calculate_autocorrelation(price_series: pd.Series, lag: int) -> float:
    returns = price_series.pct_change()
    return returns.autocorr(lag=lag)

if __name__ == "__main__":
    # Si un argument est fourni, l'utiliser comme nom de dossier
    # Sinon, utiliser "backtests" par défaut
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "backtests"
    clear_backtests_logs(target_dir)