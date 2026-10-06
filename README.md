# Kass — copia pulita per questo PC

Avvia Kass.exe oppure Avvia Kass.cmd. Mantieni tutte le cartelle insieme.
La copia include MP3, playlist, preferiti, cronologia e regolazioni.
La cartella .local contiene libreria, database e runtime: è necessaria.
Questa copia è indipendente dall’originale: nuove modifiche non si sincronizzano.
Apri una sola copia alla volta: usano le stesse porte locali.

Rimossi sorgenti frontend, dipendenze di sviluppo, test, Docker, file di build
e vecchi log. L’interfaccia è già compilata; l’avvio non esegue npm install
o la ricompilazione dell’interfaccia. La preparazione della finestra avviene
in parallelo ai servizi; ridotte le attese di disponibilità.

Widget OBS: http://127.0.0.1:8765/overlay/ (480 × 200).
Gli originali MP3 restano intatti; il volume automatico usa copie locali.
I nomi tecnici cass.json e database cass restano per compatibilità.
Questa cartella contiene dati personali: conservala sul tuo PC.

Stato di ascolto: Kass conserva sul PC l'ultima schermata, il brano e la posizione, la coda, la ripetizione e la riproduzione casuale. Alla riapertura il brano resta in pausa, pronto a riprendere dallo stesso punto. Il salvataggio si aggiorna ogni secondo e alla chiusura.
